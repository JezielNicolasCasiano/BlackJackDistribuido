"""Operaciones sobre partidas: cada una es una sola transacción en PostgreSQL.

Aquí se combinan las reglas (reglas.py) con la base de datos. Las rutas de
main.py solo traducen lo que devuelven estas funciones a respuestas HTTP.
"""
import os
import random

from psycopg import errors
from psycopg.types.json import Jsonb

from reglas import (CREAR, CRUPIER, EN_JUEGO, JUGADOR, PEDIR, PLANTARSE, TERMINADA, aplicar,
                    calcular_total, crear_mazo, repartir)

# La llave primaria de solicitudes: cada clave de idempotencia se guarda una sola vez
CLAVE_UNICA = "r2_clave_unica"

# Interruptor del experimento de control. Con "on" (lo normal) cada operación
# busca primero su clave y un reintento recibe la respuesta guardada. "off"
# existe para ver qué pasa sin la clave: reintentar crear hace otra partida, y
# reintentar pedir o plantarse recibe 409 aunque la acción sí se aplicó. Al
# guardar, el duplicado se ignora (ON CONFLICT DO NOTHING) en lugar de chocar
# con r2_clave_unica, que también delataría el reintento. Se consulta al
# momento de cada operación, así que las pruebas pueden cambiarlo.
IDEMPOTENCIA = os.environ.get("IDEMPOTENCIA", "on").lower() != "off"

GUARDAR_SOLICITUD = ("INSERT INTO solicitudes (clave, partida_id, operacion, respuesta) "
                     "VALUES (%s, %s, %s, %s)")


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
    visibles = cartas_crupier if estado == TERMINADA else cartas_crupier[:1]
    return {
        "id": str(id_partida),
        "estado": estado,
        "version": version,
        "jugador": {"cartas": cartas_jugador, "total": calcular_total(cartas_jugador)},
        "crupier": {"cartas_visibles": visibles, "total_visible": calcular_total(visibles)},
        "resultado": resultado,
    }


def _vista_de(id_partida, version, jugada):
    """La vista de una partida recién repartida o recién jugada (una reglas.Jugada)."""
    return vista(id_partida, jugada.estado, version, jugada.jugador, jugada.crupier,
                 jugada.resultado)


# ---------------- Idempotencia ----------------

def _buscar_reintento(conexion, clave, operacion, id_partida=None):
    """La respuesta guardada si la clave ya se aplicó, o None si es nueva.

    Si la clave existe pero es de otra operación o de otra partida, no es un
    reintento sino un error del cliente. Con IDEMPOTENCIA apagada no se busca.
    """
    if not IDEMPOTENCIA:
        return None
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


def _guardar_solicitud(conexion, clave, id_partida, operacion, cuerpo):
    """La clave y su respuesta, en la misma transacción que la operación."""
    sql = GUARDAR_SOLICITUD + ("" if IDEMPOTENCIA else " ON CONFLICT (clave) DO NOTHING")
    conexion.execute(sql, (clave, id_partida, operacion, Jsonb(cuerpo)))


def _respuesta_tras_choque(pool, error, clave, operacion, id_partida=None):
    """Respuesta cuando guardar la clave chocó con r2_clave_unica.

    Otra solicitud con la misma clave la guardó primero: esta esperó a que
    confirmara, chocó, y su transacción ya se deshizo sin dejar nada. Se
    responde lo que guardó la otra, o 422 si era de otra operación u otra
    partida. Cualquier otra violación de unicidad es un error real y se re-lanza.
    """
    if error.diag.constraint_name != CLAVE_UNICA:
        raise error
    with pool.connection() as conexion:
        return 200, _buscar_reintento(conexion, clave, operacion, id_partida)


# ---------------- Cartas ----------------

def _cartas(conexion, id_partida):
    """Las cartas de una partida en el orden en que salieron, separadas por dueño."""
    filas = conexion.execute(
        "SELECT duenio, carta FROM cartas WHERE partida_id = %s ORDER BY orden", (id_partida,)
    ).fetchall()
    return ([c for d, c in filas if d == JUGADOR], [c for d, c in filas if d == CRUPIER])


def _guardar_cartas(conexion, id_partida, primera_orden, pares):
    """Inserta los pares (dueño, carta) que salieron del mazo, desde primera_orden.

    orden es la posición de la carta en el mazo (empieza en 1), así que
    siempre se cumple mazo[orden] = carta (R5).
    """
    with conexion.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO cartas (partida_id, orden, duenio, carta) VALUES (%s, %s, %s, %s)",
            [(id_partida, orden, duenio, carta)
             for orden, (duenio, carta) in enumerate(pares, start=primera_orden)],
        )


# ---------------- Operaciones ----------------

def consultar_partida(pool, id_partida):
    """El estado actual de una partida. Solo lee: no bloquea ni cambia nada.

    Son dos lecturas (la partida y sus cartas). En READ COMMITTED cada una ve
    los datos confirmados hasta ese momento, y un pedir confirmado entre ambas
    daría la versión anterior con las cartas nuevas. REPEATABLE READ toma una
    sola foto de la BD en la primera lectura y la usa para las dos.
    """
    with pool.connection() as conexion:
        conexion.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        fila = conexion.execute(
            "SELECT estado, version, resultado FROM partidas WHERE id = %s", (id_partida,)
        ).fetchone()
        if fila is None:
            raise NoExiste()
        jugador, crupier = _cartas(conexion, id_partida)
    estado, version, resultado = fila
    return 200, vista(id_partida, estado, version, jugador, crupier, resultado)


def crear_partida(pool, clave, nombre, rng=random):
    """Crea una partida a nombre del jugador y reparte. Devuelve (código HTTP, cuerpo).

    201 si se creó, 200 si la clave ya se había aplicado (reintento): en ese
    caso no se crea nada y se devuelve la misma respuesta de la primera vez.
    """
    try:
        with pool.connection() as conexion, conexion.transaction():
            # 1. ¿Es un reintento? Todavía no existe una fila de partida que
            #    bloquear, el respaldo para reintentos simultáneos es la llave
            #    primaria de solicitudes (ver el except de abajo)
            guardada = _buscar_reintento(conexion, clave, CREAR)
            if guardada is not None:
                return 200, guardada

            # 2. Mezclar y repartir. Con blackjack la partida nace terminada;
            #    terminada_en lo pone el código: R3 no lo vigila
            mazo = crear_mazo(rng)
            jugada = repartir(mazo)
            id_partida, version = conexion.execute(
                """INSERT INTO partidas (jugador, estado, mazo, pos_mazo, resultado, terminada_en)
                   VALUES (%s, %s, %s, %s, %s, CASE WHEN %s THEN now() END)
                   RETURNING id, version""",
                (nombre, jugada.estado, mazo, len(jugada.nuevas), jugada.resultado,
                 jugada.terminada),
            ).fetchone()
            _guardar_cartas(conexion, id_partida, 1, jugada.nuevas)

            # 3. La clave y su respuesta, en la misma transacción que la partida
            cuerpo = _vista_de(id_partida, version, jugada)
            _guardar_solicitud(conexion, clave, id_partida, CREAR, cuerpo)
        return 201, cuerpo

    except errors.UniqueViolation as error:
        # Dos solicitudes con la misma clave al mismo tiempo: ninguna encontró
        # la clave al empezar y la segunda chocó al guardarla
        return _respuesta_tras_choque(pool, error, clave, CREAR)


def pedir(pool, id_partida, clave, version):
    return _actuar(pool, id_partida, clave, version, PEDIR)


def plantarse(pool, id_partida, clave, version):
    return _actuar(pool, id_partida, clave, version, PLANTARSE)


def _bloquear(conexion, id_partida):
    """Lee la partida y bloquea su fila hasta el final de la transacción.

    Otra acción sobre la misma partida espera aquí a que esta confirme o se
    deshaga (el semáforo del primer proyecto). Devuelve (estado, versión,
    mazo, pos_mazo, resultado).
    """
    fila = conexion.execute(
        "SELECT estado, version, mazo, pos_mazo, resultado FROM partidas WHERE id = %s FOR UPDATE",
        (id_partida,),
    ).fetchone()
    if fila is None:
        raise NoExiste()
    return fila


def _guardar_jugada(conexion, id_partida, pos_mazo, jugada):
    """Las cartas nuevas y el estado en que quedó la partida; la versión sube en uno."""
    _guardar_cartas(conexion, id_partida, pos_mazo + 1, jugada.nuevas)
    conexion.execute(
        """UPDATE partidas
           SET version = version + 1, pos_mazo = %s, estado = %s, resultado = %s,
               terminada_en = CASE WHEN %s THEN now() END
           WHERE id = %s""",
        (pos_mazo + len(jugada.nuevas), jugada.estado, jugada.resultado, jugada.terminada,
         id_partida),
    )


def _actuar(pool, id_partida, clave, version, operacion):
    """pedir o plantarse, en una sola transacción.

    Devuelve (código HTTP, cuerpo): 200 si se aplicó o si es un reintento de
    algo ya aplicado, 409 con el estado actual si la versión no coincide o la
    partida ya terminó.
    """
    try:
        with pool.connection() as conexion, conexion.transaction():
            # 1. Bloquear la fila de la partida
            estado, version_actual, mazo, pos_mazo, resultado = _bloquear(conexion, id_partida)

            # 2. La clave antes que la versión: un reintento llega con la versión
            #    vieja, porque el cliente nunca recibió la nueva
            guardada = _buscar_reintento(conexion, clave, operacion, id_partida)
            if guardada is not None:
                return 200, guardada

            # 3. ¿Lo que vio el jugador sigue siendo cierto? Si no, no se aplica
            #    nada y se le devuelve el estado real para que vuelva a decidir
            jugador, crupier = _cartas(conexion, id_partida)
            if estado != EN_JUEGO or version != version_actual:
                return 409, vista(id_partida, estado, version_actual, jugador, crupier, resultado)

            # 4. Aplicar las reglas y guardar el resultado junto con la clave
            jugada = aplicar(operacion, jugador, crupier, mazo, pos_mazo)
            _guardar_jugada(conexion, id_partida, pos_mazo, jugada)
            cuerpo = _vista_de(id_partida, version_actual + 1, jugada)
            _guardar_solicitud(conexion, clave, id_partida, operacion, cuerpo)
        # 5. Confirmado (al salir del with). Si el pod muere después de aquí, el
        #    reintento cae en el paso 2
        return 200, cuerpo

    except errors.UniqueViolation as error:
        # Con el bloqueo, dos reintentos de la misma partida no llegan a chocar.
        # Solo ocurre si la misma clave se usó en otra partida al mismo tiempo
        return _respuesta_tras_choque(pool, error, clave, operacion, id_partida)
