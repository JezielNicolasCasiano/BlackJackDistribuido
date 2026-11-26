"""Operaciones sobre partidas: cada una es una sola transacción en PostgreSQL.

Aquí se combinan las reglas (reglas.py) con la base de datos. Las rutas de
main.py solo traducen lo que devuelven estas funciones a respuestas HTTP.
"""
import os
import random

from psycopg import errors
from psycopg.types.json import Jsonb

from reglas import (calcular_resultado, calcular_total, crear_mazo, es_blackjack, jugar_crupier,
                    reparto_inicial, se_paso, turno_terminado)


# Interruptor del experimento de control (diseño, sección 9.3). Con "off" no se
# busca la clave y, al guardarla, un duplicado se ignora (ON CONFLICT DO NOTHING)
# en lugar de chocar con r2_clave_unica, que también delataría el reintento.
IDEMPOTENCIA = os.environ.get("IDEMPOTENCIA", "on").lower() != "off"

GUARDAR_SOLICITUD = ("INSERT INTO solicitudes (clave, partida_id, operacion, respuesta) "
                     "VALUES (%s, %s, %s, %s)")


def _guardar_solicitud(conexion, clave, id_partida, operacion, cuerpo):
    sql = GUARDAR_SOLICITUD + ("" if IDEMPOTENCIA else " ON CONFLICT (clave) DO NOTHING")
    conexion.execute(sql, (clave, id_partida, operacion, Jsonb(cuerpo)))


class ClaveReutilizada(Exception):
    """La clave de idempotencia ya se usó para otra operación u otra partida."""


class NoExiste(Exception):
    """La partida no existe."""


def vista(id_partida, estado, version, cartas_jugador, cartas_crupier, resultado):
    """El estado de una partida tal como lo ve el jugador.

    Mientras se juega, del crupier solo se ve su primera carta. Al terminar se
    ven todas, con el mismo nombre de campo: "visibles" es lo que el jugador
    puede ver en ese momento.
    """
    visibles = cartas_crupier if estado == "terminada" else cartas_crupier[:1]
    return {
        "id": str(id_partida),
        "estado": estado,
        "version": version,
        "jugador": {"cartas": cartas_jugador, "total": calcular_total(cartas_jugador)},
        "crupier": {"cartas_visibles": visibles, "total_visible": calcular_total(visibles)},
        "resultado": resultado,
    }


def _respuesta_guardada(conexion, clave, operacion, id_partida=None):
    """La respuesta de una acción ya aplicada, o None si la clave es nueva.

    Si la clave existe pero es de otra operación o de otra partida, no es un
    reintento sino un error del cliente.
    """
    fila = conexion.execute(
        "SELECT operacion, partida_id, respuesta FROM solicitudes WHERE clave = %s", (clave,)
    ).fetchone()
    if fila is None:
        return None
    if fila[0] != operacion:
        raise ClaveReutilizada(f"la clave ya se usó para '{fila[0]}'")
    if id_partida is not None and str(fila[1]) != str(id_partida):
        raise ClaveReutilizada("la clave ya se usó en otra partida")
    return fila[2]


def _cartas(conexion, id_partida):
    """Las cartas de una partida en el orden en que salieron, separadas por dueño."""
    filas = conexion.execute(
        "SELECT duenio, carta FROM cartas WHERE partida_id = %s ORDER BY orden", (id_partida,)
    ).fetchall()
    return ([c for d, c in filas if d == "jugador"], [c for d, c in filas if d == "crupier"])


def consultar_partida(pool, id_partida):
    """El estado actual de una partida. Solo lee: no bloquea ni cambia nada."""
    with pool.connection() as conexion:
        fila = conexion.execute(
            "SELECT estado, version, resultado FROM partidas WHERE id = %s", (id_partida,)
        ).fetchone()
        if fila is None:
            raise NoExiste()
        jugador, crupier = _cartas(conexion, id_partida)
    estado, version, resultado = fila
    return 200, vista(id_partida, estado, version, jugador, crupier, resultado)


def crear_partida(pool, clave, jugador, rng=random):
    """Crea una partida y reparte. Devuelve (código HTTP, cuerpo).

    201 si se creó, 200 si la clave ya se había aplicado (reintento): en ese
    caso no se crea nada y se devuelve la misma respuesta de la primera vez.
    """
    try:
        with pool.connection() as conexion, conexion.transaction():
            # 1. ¿Es un reintento? Todavía no existe una fila de partida que
            #    bloquear, el respaldo para reintentos simultáneos es la llave
            #    primaria de solicitudes (ver el except de abajo)
            guardada = _respuesta_guardada(conexion, clave, "crear") if IDEMPOTENCIA else None
            if guardada is not None:
                return 200, guardada

            # 2. Mezclar y repartir
            mazo = crear_mazo(rng)
            reparto = reparto_inicial(mazo)
            jugador_cartas = [c for duenio, c in reparto if duenio == "jugador"]
            crupier_cartas = [c for duenio, c in reparto if duenio == "crupier"]

            # 3. Con blackjack del jugador la partida termina al crearse, sin
            #    que juegue el crupier. terminada_en lo pone el código: R3 no lo vigila
            terminada = es_blackjack(jugador_cartas)
            estado = "terminada" if terminada else "en_juego"
            resultado = calcular_resultado(jugador_cartas, crupier_cartas) if terminada else None

            id_partida, version = conexion.execute(
                """INSERT INTO partidas (jugador, estado, mazo, pos_mazo, resultado, terminada_en)
                   VALUES (%s, %s, %s, %s, %s, CASE WHEN %s THEN now() END)
                   RETURNING id, version""",
                (jugador, estado, mazo, len(reparto), resultado, terminada),
            ).fetchone()
            with conexion.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO cartas (partida_id, orden, duenio, carta) VALUES (%s, %s, %s, %s)",
                    [(id_partida, orden, duenio, carta)
                     for orden, (duenio, carta) in enumerate(reparto, start=1)],
                )

            # 4. La clave y su respuesta, en la misma transacción que la partida
            cuerpo = vista(id_partida, estado, version, jugador_cartas, crupier_cartas, resultado)
            _guardar_solicitud(conexion, clave, id_partida, "crear", cuerpo)
        return 201, cuerpo

    except errors.UniqueViolation as error:
        # Dos solicitudes con la misma clave al mismo tiempo: la segunda esperó
        # a que la primera confirmara y chocó con r2_clave_unica. Su transacción
        # ya se deshizo; se responde lo que guardó la primera.
        if error.diag.constraint_name != "r2_clave_unica":
            raise
        with pool.connection() as conexion:
            return 200, _respuesta_guardada(conexion, clave, "crear")


def pedir(pool, id_partida, clave, version):
    return _actuar(pool, id_partida, clave, version, "pedir")


def plantarse(pool, id_partida, clave, version):
    return _actuar(pool, id_partida, clave, version, "plantarse")


def _actuar(pool, id_partida, clave, version, operacion):
    """pedir o plantarse, con los pasos del diseño en una sola transacción.

    Devuelve (código HTTP, cuerpo): 200 si se aplicó o si es un reintento de
    algo ya aplicado, 409 con el estado actual si la versión no coincide o la
    partida ya terminó.
    """
    try:
        with pool.connection() as conexion, conexion.transaction():
            # 1. Bloquear la fila. Otra acción sobre la misma partida espera aquí
            #    a que esta confirme o se deshaga (el semáforo del primer proyecto)
            fila = conexion.execute(
                """SELECT estado, version, mazo, pos_mazo, resultado
                   FROM partidas WHERE id = %s FOR UPDATE""",
                (id_partida,),
            ).fetchone()
            if fila is None:
                raise NoExiste()
            estado, version_actual, mazo, pos_mazo, resultado = fila

            # 2. La clave antes que la versión: un reintento llega con la versión
            #    vieja, porque el cliente nunca recibió la nueva
            guardada = (_respuesta_guardada(conexion, clave, operacion, id_partida)
                        if IDEMPOTENCIA else None)
            if guardada is not None:
                return 200, guardada

            jugador, crupier = _cartas(conexion, id_partida)

            # 3. ¿Lo que vio el jugador sigue siendo cierto? Si no, no se aplica
            #    nada y se le devuelve el estado real para que vuelva a decidir
            if estado != "en_juego" or version != version_actual:
                return 409, vista(id_partida, estado, version_actual, jugador, crupier, resultado)

            # 4. Aplicar. mazo viene como lista de Python (empieza en 0), así que
            #    la siguiente carta es mazo[pos_mazo]; en SQL sería mazo[pos_mazo + 1]
            nuevas = []                       # (dueño, carta) en el orden en que salen
            if operacion == "pedir":
                jugador.append(mazo[pos_mazo])
                nuevas.append(("jugador", mazo[pos_mazo]))

            # Pasarse termina la partida sin que juegue el crupier. Plantarse o
            # llegar a 21 terminan el turno, y entonces juega el crupier
            if se_paso(jugador):
                estado = "terminada"
            elif operacion == "plantarse" or turno_terminado(jugador):
                siguientes = mazo[pos_mazo + len(nuevas):]
                for carta in jugar_crupier(crupier, siguientes):
                    crupier.append(carta)
                    nuevas.append(("crupier", carta))
                estado = "terminada"
            terminada = estado == "terminada"
            resultado = calcular_resultado(jugador, crupier) if terminada else None

            with conexion.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO cartas (partida_id, orden, duenio, carta) VALUES (%s, %s, %s, %s)",
                    [(id_partida, pos_mazo + i, duenio, carta)
                     for i, (duenio, carta) in enumerate(nuevas, start=1)],
                )
            conexion.execute(
                """UPDATE partidas
                   SET version = version + 1, pos_mazo = %s, estado = %s, resultado = %s,
                       terminada_en = CASE WHEN %s THEN now() END
                   WHERE id = %s""",
                (pos_mazo + len(nuevas), estado, resultado, terminada, id_partida),
            )
            cuerpo = vista(id_partida, estado, version_actual + 1, jugador, crupier, resultado)
            _guardar_solicitud(conexion, clave, id_partida, operacion, cuerpo)
        # 5. Confirmado (al salir del with). Si el pod muere después de aquí, el
        #    reintento cae en el paso 2
        return 200, cuerpo

    except errors.UniqueViolation as error:
        # Con el bloqueo, dos reintentos de la misma partida no llegan a chocar.
        # Solo ocurre si la misma clave se usó en otra partida al mismo tiempo
        if error.diag.constraint_name != "r2_clave_unica":
            raise
        with pool.connection() as conexion:
            return 200, _respuesta_guardada(conexion, clave, operacion, id_partida)
