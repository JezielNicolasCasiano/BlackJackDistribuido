#!/usr/bin/env python3
"""Verificación posterior a cada prueba (secciones 7.2 y 7.3 del documento).

R1 a R4 son restricciones de bd/esquema.sql: la propia BD las impide. Aquí se verifican
R5 a R8 y se compara lo que vio k6 con lo que guardó la BD: operaciones perdidas,
duplicadas y aplicadas sin confirmar.

Conexión: variables estándar de libpq (PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE).
Por omisión usa los valores de k8s/10-config.yaml con un port-forward a localhost:55433.
Uso:  python pruebas/verificar_integridad.py --k6-json k6.json.gz --salida verificacion.json
"""
import argparse
import gzip
import json
import os
import sys
from collections import defaultdict

import psycopg

# Reglas reales del juego (procesador/reglas.py)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "procesador"))
from reglas import calcular_resultado, calcular_total  # noqa: E402

CONEXION = dict(
    host=os.getenv("PGHOST", "127.0.0.1"),
    port=os.getenv("PGPORT", "55433"),
    user=os.getenv("PGUSER", "blackjack"),
    password=os.getenv("PGPASSWORD", "blackjack-lab"),
    dbname=os.getenv("PGDATABASE", "blackjack"),
)


def cargar(conn):
    """Los UUID se leen como str para poder compararlos con lo que registró k6."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, estado, mazo, pos_mazo, resultado, jugador FROM partidas")
        partidas = {str(r[0]): r for r in cur.fetchall()}
        cur.execute("SELECT partida_id, orden, duenio, carta FROM cartas ORDER BY partida_id, orden")
        cartas = defaultdict(list)
        for pid, orden, duenio, carta in cur.fetchall():
            cartas[str(pid)].append((orden, duenio, carta))
        cur.execute("SELECT clave, partida_id, operacion, creada_en FROM solicitudes")
        solicitudes = [(str(c), str(p), op, t) for c, p, op, t in cur.fetchall()]
    return partidas, cartas, solicitudes


def verificar_reglas(partidas, cartas, solicitudes):
    v = {"R5": [], "R6": [], "R7": [], "R8": []}
    pedir = defaultdict(list)
    plantarse = {}
    for _clave, pid, op, t in solicitudes:
        if op == "pedir":
            pedir[pid].append(t)
        elif op == "plantarse":
            plantarse[pid] = min(t, plantarse.get(pid, t))

    for pid, (_, estado, mazo, pos, resultado, _jugador) in partidas.items():
        mazo = list(mazo)
        ordenes = [o for o, _, _ in cartas[pid]]
        todas = [c for _, _, c in cartas[pid]]
        jug = [c for _, d, c in cartas[pid] if d == "jugador"]
        cru = [c for _, d, c in cartas[pid] if d == "crupier"]

        # R5: las cartas repartidas son las primeras pos_mazo del mazo, en orden (orden = 1, 2, 3...)
        if len(todas) != pos or todas != mazo[:pos] or ordenes != list(range(1, pos + 1)):
            v["R5"].append(pid)

        # R6: cartas del jugador = 2 + número de pedir aplicados
        if len(jug) != 2 + len(pedir[pid]):
            v["R6"].append(pid)

        # R7: ninguna carta del jugador tras llegar a 21 o pasarse, ni acciones tras plantarse
        malo = any(i >= 2 and calcular_total(jug[:i]) >= 21 for i in range(len(jug)))
        if pid in plantarse and any(t > plantarse[pid] for t in pedir[pid]):
            malo = True
        if malo:
            v["R7"].append(pid)

        # R8: el resultado guardado coincide con el que se recalcula con las cartas
        if estado == "terminada" and calcular_resultado(jug, cru) != resultado:
            v["R8"].append(pid)
    return v


def leer_k6(ruta):
    """clave -> {'op', 'aceptada'}. Una clave está aceptada si algún intento recibió 2xx."""
    abrir = gzip.open if ruta.endswith(".gz") else open
    claves = {}
    with abrir(ruta, "rt", encoding="utf-8") as f:
        for linea in f:
            if '"http_reqs"' not in linea or '"clave"' not in linea:
                continue
            d = json.loads(linea)
            if d.get("type") != "Point" or d.get("metric") != "http_reqs":
                continue
            tags = d["data"]["tags"]
            k = tags.get("clave")
            if not k:
                continue
            ok = str(tags.get("status", "")).startswith("2")
            e = claves.setdefault(k, {"op": tags.get("op"), "aceptada": False})
            e["aceptada"] = e["aceptada"] or ok
    return claves


def comparar_con_k6(claves_k6, partidas, solicitudes, r6_violaciones):
    en_bd = {s[0] for s in solicitudes}
    perdidas = [k for k, e in claves_k6.items() if e["aceptada"] and k not in en_bd]
    sin_confirmar = [k for k in en_bd if k in claves_k6 and not claves_k6[k]["aceptada"]]
    # Partidas creadas más de una vez para una sola decisión: k6 pone el nombre k6-<clave>
    por_jugador = defaultdict(int)
    for p in partidas.values():
        if str(p[5]).startswith("k6-"):
            por_jugador[p[5]] += 1
    dup_partidas = sum(n - 1 for n in por_jugador.values() if n > 1)
    return {
        "perdidas": len(perdidas),
        "duplicadas": dup_partidas + r6_violaciones,
        "duplicadas_partidas_creadas_dos_veces": dup_partidas,
        "duplicadas_cartas_de_mas_R6": r6_violaciones,
        "aplicadas_sin_confirmar": len(sin_confirmar),
        "claves_k6": len(claves_k6),
        "ejemplos_perdidas": perdidas[:5],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k6-json", help="salida de k6 (--out json=archivo.json.gz)")
    ap.add_argument("--salida", default="verificacion.json")
    a = ap.parse_args()

    with psycopg.connect(**CONEXION) as conn:
        partidas, cartas, solicitudes = cargar(conn)
    v = verificar_reglas(partidas, cartas, solicitudes)
    resumen = {
        "partidas": len(partidas),
        "solicitudes": len(solicitudes),
        "violaciones": {r: len(ids) for r, ids in v.items()},
        "violaciones_total": sum(len(ids) for ids in v.values()),
        "ejemplos": {r: ids[:5] for r, ids in v.items() if ids},
    }
    if a.k6_json:
        resumen["comparacion_k6"] = comparar_con_k6(
            leer_k6(a.k6_json), partidas, solicitudes, len(v["R6"]))

    with open(a.salida, "w", encoding="utf-8") as f:
        json.dump(resumen, f, indent=2, ensure_ascii=False)
    print(json.dumps(resumen, indent=2, ensure_ascii=False))

    c = resumen.get("comparacion_k6", {})
    mal = resumen["violaciones_total"] or c.get("perdidas", 0) or c.get("duplicadas", 0)
    sys.exit(1 if mal else 0)


if __name__ == "__main__":
    main()
