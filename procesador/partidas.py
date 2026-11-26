"""Operaciones sobre partidas: cada una es una sola transacción en PostgreSQL.

Aquí se combinan las reglas (reglas.py) con la base de datos. Las rutas de
main.py solo traducen lo que devuelven estas funciones a respuestas HTTP.
"""
import random

from psycopg import errors
from psycopg.types.json import Jsonb

from reglas import calcular_resultado, calcular_total, crear_mazo, es_blackjack, reparto_inicial


class ClaveReutilizada(Exception):
    """La clave de idempotencia ya se usó para otra operación."""


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


def _respuesta_guardada(conexion, clave, operacion):
    """La respuesta de una acción ya aplicada, o None si la clave es nueva."""
    fila = conexion.execute(
        "SELECT operacion, respuesta FROM solicitudes WHERE clave = %s", (clave,)
    ).fetchone()
    if fila is None:
        return None
    if fila[0] != operacion:
        raise ClaveReutilizada(f"la clave ya se usó para '{fila[0]}'")
    return fila[1]


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
            guardada = _respuesta_guardada(conexion, clave, "crear")
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
            conexion.execute(
                "INSERT INTO solicitudes (clave, partida_id, operacion, respuesta) VALUES (%s, %s, 'crear', %s)",
                (clave, id_partida, Jsonb(cuerpo)),
            )
        return 201, cuerpo

    except errors.UniqueViolation as error:
        # Dos solicitudes con la misma clave al mismo tiempo: la segunda esperó
        # a que la primera confirmara y chocó con r2_clave_unica. Su transacción
        # ya se deshizo; se responde lo que guardó la primera.
        if error.diag.constraint_name != "r2_clave_unica":
            raise
        with pool.connection() as conexion:
            return 200, _respuesta_guardada(conexion, clave, "crear")
