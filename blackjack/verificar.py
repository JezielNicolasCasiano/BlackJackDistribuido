"""Cuenta las partidas que incumplen las reglas R5 a R8 del diseño."""

import json
import os

from psycopg import connect
from psycopg.rows import dict_row

from reglas import resultado_codigos, total_codigos


def violaciones(partida):
    cartas = partida["cartas"]
    posiciones = partida["posiciones"]
    jugador = partida["jugador"]
    crupier = partida["crupier"]
    cantidad = partida["pos_mazo"]
    jugador_al_plantarse = partida["jugador_al_plantarse"]
    return {
        "R5": cartas != partida["mazo"][:cantidad]
        or posiciones != list(range(1, cantidad + 1)),
        "R6": len(jugador) != 2 + partida["pedidos"],
        "R7": any(total_codigos(jugador[:n]) >= 21 for n in range(2, len(jugador)))
        or (jugador_al_plantarse is not None and len(jugador) > jugador_al_plantarse),
        "R8": partida["estado"] == "terminada"
        and partida["resultado"] != resultado_codigos(jugador, crupier),
    }


def verificar(conn):
    conteos = dict.fromkeys(("R5", "R6", "R7", "R8"), 0)
    filas = conn.execute(
        """
        SELECT p.estado, p.resultado, p.mazo, p.pos_mazo,
               c.cartas, c.posiciones, c.jugador, c.crupier,
               s.pedidos, s.jugador_al_plantarse
        FROM partidas p
        CROSS JOIN LATERAL (
            SELECT ARRAY(SELECT carta FROM cartas WHERE partida_id = p.id ORDER BY orden) cartas,
                   ARRAY(SELECT orden FROM cartas WHERE partida_id = p.id ORDER BY orden) posiciones,
                   ARRAY(SELECT carta FROM cartas
                         WHERE partida_id = p.id AND dueno = 'jugador' ORDER BY orden) jugador,
                   ARRAY(SELECT carta FROM cartas
                         WHERE partida_id = p.id AND dueno = 'crupier' ORDER BY orden) crupier
        ) c
        CROSS JOIN LATERAL (
            SELECT count(*) FILTER (WHERE operacion = 'pedir') pedidos,
                   min(jsonb_array_length(respuesta #> '{jugador,cartas}'))
                       FILTER (WHERE operacion = 'plantarse') jugador_al_plantarse
            FROM solicitudes WHERE partida_id = p.id
        ) s
        """
    )
    for partida in filas:
        for regla, incumplida in violaciones(partida).items():
            conteos[regla] += incumplida
    return conteos


if __name__ == "__main__":
    with connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        resultado = verificar(conn)
    print(json.dumps(resultado))
    raise SystemExit(int(any(resultado.values())))
