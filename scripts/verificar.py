"""Verificación de integridad después de una prueba.

Uso: python3 scripts/verificar.py DIRECTORIO
     python3 scripts/verificar.py --revisiones   (lista las revisiones y qué cubre cada una)

Lee la copia de las tablas que deja verificar.sh en DIRECTORIO/bd/ y, si
existe, el registro de claves de k6 (DIRECTORIO/claves.jsonl). Escribe
DIRECTORIO/verificacion.csv (una fila por revisión) y
DIRECTORIO/verificacion_detalle.csv (cada violación con su partida o clave).

Reutiliza las reglas del juego del procesador (procesador/reglas.py), así que
lo que se verifica es lo mismo que el procesador debió hacer. Solo usa la
biblioteca estándar: corre con el python3 del sistema.
"""
import csv
import gzip
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from typing import NamedTuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "procesador"))
from reglas import (CREAR, CRUPIER, EN_JUEGO, GANADA, JUGADOR, PEDIR, PERDIDA, PLANTARSE,  # noqa: E402
                    TERMINADA, EMPATE, calcular_resultado, calcular_total, es_blackjack)

ESTADOS = {EN_JUEGO, TERMINADA}
RESULTADOS = {GANADA, PERDIDA, EMPATE}
DUENIOS = {JUGADOR, CRUPIER}
OPERACIONES = {CREAR, PEDIR, PLANTARSE}
CARTA = re.compile(r"^(A|[2-9]|10|J|Q|K)[CDTP]$")


class Def(NamedTuple):
    """Una revisión: lo que sale en verificacion.csv y en la pantalla."""
    id: str
    grupo: str
    descripcion: str   # texto de la columna "descripcion" del CSV
    gravedad: str      # VIOLACION, AVISO, INFO (ver abajo)
    casos: tuple = ()  # cada situación distinta que acusa el mismo ID


# Gravedad de cada revisión:
#   VIOLACION: se marca con !! si no es 0 y entra en el total de violaciones.
#   AVISO:     se marca con !! si no es 0, pero no entra en el total (D y D2 repiten
#              partidas que R6 y D1 ya cuentan; sumarlas las contaría dos veces).
#              S3 también es AVISO: delata un registro de k6 incompleto, un problema
#              del instrumento y no de la aplicación, pero hay que verlo.
#   INFO:      nunca se marca ni se suma (cifras de la corrida y controles de k6).
#              S1 es INFO: bajo fallos es esperable que una acción se aplique sin que
#              el cliente reciba la confirmación, y tiene su propia cuenta.
# La gravedad solo decide la marca y el total en pantalla; no sale en los CSV.
VIOLACION, AVISO, INFO = "violacion", "aviso", "info"

REVISIONES = [
    Def("N1", "datos", "Partidas", INFO),
    Def("N2", "datos", "Partidas terminadas", INFO),
    Def("N3", "datos", "Cartas", INFO),
    Def("N4", "datos", "Solicitudes", INFO),
    Def("R1", "reglas", "Carta única: cartas repetidas en una partida", VIOLACION),
    Def("R2", "reglas", "Clave única: claves aplicadas más de una vez", VIOLACION),
    Def("R3", "reglas", "Fin con resultado: terminada sin resultado o al revés", VIOLACION),
    Def("R4", "reglas", "Valores válidos: estado, resultado, dueño, carta u operación inválidos",
        VIOLACION, ("estado, resultado, dueño o carta inválidos en una partida (se acusa por partida)",
                    "operación inválida en una solicitud (se acusa por clave)")),
    Def("R5", "reglas", "Cartas según el mazo: no coinciden con mazo[orden] o con pos_mazo",
        VIOLACION, ("órdenes con huecos", "cantidad de cartas distinta de pos_mazo",
                    "carta distinta de mazo[orden]")),
    Def("R6", "reglas", "Cartas según las acciones: cartas del jugador distintas de 2 + pedir",
        VIOLACION),
    Def("R7", "reglas", "Sin cartas tras terminar: cartas o acciones después de 21, pasarse o plantarse",
        VIOLACION, ("carta del jugador con el turno ya terminado (21, pasarse o blackjack)",
                    "reparto inicial en otro orden",
                    "más de un plantarse aplicado",
                    "acciones aplicadas después de plantarse")),
    Def("R8", "reglas", "Resultado correcto: distinto del que calcula calcular_resultado", VIOLACION),
    Def("A1", "adicionales", "terminada_en no coincide con el estado (R3 no lo vigila)", VIOLACION),
    Def("A2", "adicionales", "Crupier que jugó cuando no debía, de más o de menos", VIOLACION,
        ("crupier con cartas de más: tomó una carta cuando no debía jugar o ya tenía 17 o más",
         "crupier con cartas de menos: la partida terminó con el crupier por debajo de 17")),
    Def("A3", "adicionales", "Estado que no corresponde a las cartas y acciones", VIOLACION),
    Def("A4", "adicionales", "Versión distinta de 1 + acciones aplicadas", VIOLACION),
    Def("P1", "k6", "Operaciones perdidas: k6 recibió 2xx y la clave no está en la BD", VIOLACION),
    Def("D1", "k6", "Partidas creadas de más (sin solicitud de crear)", VIOLACION),
    Def("D2", "k6", "Cartas del jugador de más (exceso de R6)", AVISO),
    Def("D", "k6", "Operaciones duplicadas: D1 + D2", AVISO),
    Def("S1", "k6", "Aplicadas sin confirmar: sin 2xx tras los reintentos, pero en la BD", INFO),
    Def("S2", "k6", "Respondidas 409 aunque se aplicaron (control, H6)", INFO),
    Def("S3", "k6", "Claves en la BD que k6 no registró (registro incompleto)", AVISO),
    Def("I1", "k6", "Acciones registradas por k6", INFO),
    Def("I2", "k6", "Acciones con reintento", INFO),
    Def("I3", "k6", "Fallidas y no aplicadas (sin efecto en la BD)", INFO),
    Def("I4", "k6", "Claves repetidas en el registro de k6 (error del script)", INFO),
]
POR_ID = {d.id: d for d in REVISIONES}
IDS = [d.id for d in REVISIONES]


def leer_csv(ruta):
    with gzip.open(ruta, "rt", newline="") as f:
        return list(csv.DictReader(f))


def leer_bd(directorio):
    bd = directorio / "bd"
    partidas = {p["id"]: p for p in leer_csv(bd / "partidas.csv.gz")}
    for p in partidas.values():
        # Arreglo de PostgreSQL: {7C,10P,...}. Las cartas no llevan comas ni comillas
        p["mazo"] = p["mazo"].strip("{}").split(",")
        p["version"] = int(p["version"])
        p["pos_mazo"] = int(p["pos_mazo"])
    cartas = defaultdict(list)
    for c in leer_csv(bd / "cartas.csv.gz"):
        cartas[c["partida_id"]].append((int(c["orden"]), c["duenio"], c["carta"]))
    for lista in cartas.values():
        lista.sort()
    solicitudes = leer_csv(bd / "solicitudes.csv.gz")
    return partidas, cartas, solicitudes


class Revision:
    """Acumula las violaciones de cada revisión y su detalle."""

    def __init__(self):
        self.filas = []        # (grupo, clave, descripcion, valor)
        self.detalle = []      # (revision, referencia, detalle)

    def agregar(self, clave, casos):
        """casos: lista de (referencia, detalle). El valor es cuántos hay."""
        d = POR_ID[clave]
        self.filas.append((d.grupo, clave, d.descripcion, len(casos)))
        self.detalle.extend((clave, ref, det) for ref, det in casos)

    def dato(self, clave, valor):
        """Una cifra, sin detalle."""
        d = POR_ID[clave]
        self.filas.append((d.grupo, clave, d.descripcion, valor))


def recorrer(cartas):
    """Repite la partida carta por carta con las reglas del juego.

    Devuelve las manos finales y las anomalías encontradas: cartas del jugador
    que no debieron salir (R7) y cartas del crupier fuera de lugar.
    """
    r7, crupier_mal = [], []
    duenios = [d for _, d, _ in cartas[:4]]
    if duenios != [JUGADOR, CRUPIER, JUGADOR, CRUPIER]:
        r7.append(f"reparto inicial en otro orden: {duenios}")
    jugador = [c for _, d, c in cartas[:4] if d == JUGADOR]
    crupier = [c for _, d, c in cartas[:4] if d == CRUPIER]
    turno_terminado = es_blackjack(jugador)
    for orden, duenio, carta in cartas[4:]:
        if duenio == JUGADOR:
            if turno_terminado or calcular_total(jugador) >= 21:
                r7.append(f"carta {carta} (orden {orden}) con el turno ya terminado, "
                          f"total {calcular_total(jugador)}")
            jugador.append(carta)
        else:
            # El crupier juega cuando el turno del jugador ya terminó
            turno_terminado = True
            if calcular_total(jugador) > 21 or es_blackjack(jugador[:2]):
                crupier_mal.append(f"el crupier tomó {carta} aunque no debía jugar")
            elif calcular_total(crupier) >= 17:
                crupier_mal.append(f"el crupier tomó {carta} con {calcular_total(crupier)}")
            crupier.append(carta)
    return jugador, crupier, r7, crupier_mal


def verificar_reglas(partidas, cartas, solicitudes, rev):
    por_partida = defaultdict(Counter)
    for s in solicitudes:
        por_partida[s["partida_id"]][s["operacion"]] += 1
    orden_acciones = defaultdict(list)
    for s in solicitudes:
        if s["operacion"] != CREAR:
            orden_acciones[s["partida_id"]].append((s["creada_en"], s["operacion"]))

    casos = defaultdict(list)
    exceso_r6 = 0
    for pid, p in partidas.items():
        mano = cartas.get(pid, [])
        ops = por_partida[pid]
        terminada = p["estado"] == TERMINADA

        # R1. Carta única
        repetidas = [c for c, n in Counter(c for _, _, c in mano).items() if n > 1]
        if repetidas:
            casos["R1"].append((pid, f"cartas repetidas: {repetidas}"))
        # R3. Fin con resultado
        if terminada != bool(p["resultado"]):
            casos["R3"].append((pid, f"estado {p['estado']}, resultado '{p['resultado']}'"))
        # R4. Valores válidos
        malos = []
        if p["estado"] not in ESTADOS:
            malos.append(f"estado {p['estado']}")
        if p["resultado"] and p["resultado"] not in RESULTADOS:
            malos.append(f"resultado {p['resultado']}")
        malos += [f"dueño {d}" for _, d, _ in mano if d not in DUENIOS]
        malos += [f"carta {c}" for _, _, c in mano if not CARTA.match(c)]
        if malos:
            casos["R4"].append((pid, ", ".join(malos)))

        # R5. Cartas según el mazo: órdenes 1..n sin huecos, n = pos_mazo y mazo[orden] = carta
        ordenes = [o for o, _, _ in mano]
        if ordenes != list(range(1, len(mano) + 1)):
            casos["R5"].append((pid, f"órdenes con huecos: {ordenes}"))
        elif len(mano) != p["pos_mazo"]:
            casos["R5"].append((pid, f"{len(mano)} cartas y pos_mazo {p['pos_mazo']}"))
        distintas = [(o, c) for o, _, c in mano if p["mazo"][o - 1] != c]
        if distintas:
            casos["R5"].append((pid, f"no coinciden con el mazo: {distintas}"))

        # R6. Cartas del jugador = 2 + pedir aplicados
        n_jugador = sum(1 for _, d, _ in mano if d == JUGADOR)
        if n_jugador != 2 + ops[PEDIR]:
            casos["R6"].append((pid, f"{n_jugador} cartas del jugador y {ops[PEDIR]} pedir"))
            exceso_r6 += max(0, n_jugador - 2 - ops[PEDIR])

        # R7. Sin cartas tras terminar (recorrido) y nada después de plantarse
        jugador, crupier, r7, crupier_mal = recorrer(mano)
        for d in r7:
            casos["R7"].append((pid, d))
        acciones = [op for _, op in sorted(orden_acciones[pid])]
        if acciones.count(PLANTARSE) > 1:
            casos["R7"].append((pid, f"{acciones.count(PLANTARSE)} plantarse aplicados"))
        elif PLANTARSE in acciones and acciones[-1] != PLANTARSE:
            casos["R7"].append((pid, f"acciones después de plantarse: {acciones}"))

        # R8. Resultado correcto
        if terminada and p["resultado"] != calcular_resultado(jugador, crupier):
            casos["R8"].append((pid, f"guardado {p['resultado']}, "
                                     f"calculado {calcular_resultado(jugador, crupier)}"))

        # ---- Revisiones adicionales ----
        if terminada != bool(p["terminada_en"]):
            casos["A1"].append((pid, f"estado {p['estado']}, terminada_en '{p['terminada_en']}'"))
        for d in crupier_mal:
            casos["A2"].append((pid, d))
        se_paso = calcular_total(jugador) > 21
        bj = es_blackjack(jugador[:2])
        if terminada and not se_paso and not bj and calcular_total(crupier) < 17:
            casos["A2"].append((pid, f"el crupier se quedó en {calcular_total(crupier)}"))
        # Debe terminar justo cuando el jugador llega a 21, se pasa, tiene blackjack o se planta
        debe_terminar = bj or calcular_total(jugador) >= 21 or ops[PLANTARSE] > 0
        if terminada != debe_terminar:
            casos["A3"].append((pid, f"estado {p['estado']} con total {calcular_total(jugador)} "
                                     f"y {ops[PLANTARSE]} plantarse"))
        # Cada acción aplicada sube la versión en 1
        if p["version"] != 1 + ops[PEDIR] + ops[PLANTARSE]:
            casos["A4"].append((pid, f"versión {p['version']} con "
                                     f"{ops[PEDIR] + ops[PLANTARSE]} acciones"))

    # R2. Clave única (la llave primaria lo impide; se cuenta por completitud)
    claves = Counter(s["clave"] for s in solicitudes)
    casos["R2"] = [(c, f"{n} veces") for c, n in claves.items() if n > 1]
    malas = [(s["clave"], f"operación {s['operacion']}")
             for s in solicitudes if s["operacion"] not in OPERACIONES]
    casos["R4"] += malas

    for d in REVISIONES:
        if d.grupo in ("reglas", "adicionales"):
            rev.agregar(d.id, casos[d.id])
    return exceso_r6


def comparar_k6(directorio, partidas, solicitudes, exceso_r6, rev):
    """Lo que vio k6 frente a lo que guardó la base de datos."""
    con_crear = {s["partida_id"] for s in solicitudes if s["operacion"] == CREAR}
    sin_crear = [(pid, "partida sin solicitud de crear") for pid in partidas if pid not in con_crear]

    # jugar.sh lo comprime al terminar; recién salido de k6 está sin comprimir
    rutas = [r for r in (directorio / "claves.jsonl", directorio / "claves.jsonl.gz") if r.exists()]
    if not rutas:
        rev.agregar("D1", sin_crear)
        return
    abrir = gzip.open if rutas[0].suffix == ".gz" else open
    with abrir(rutas[0], "rt") as f:
        # Solo las líneas JSON: k6 podría escribir algún aviso en el mismo archivo
        registros = [json.loads(linea) for linea in f if linea.startswith("{")]
    en_bd = {s["clave"]: s for s in solicitudes}
    vistas = {r["clave"] for r in registros}

    perdidas = [(r["clave"], f"{r['op']} confirmada con {r['codigo']} y ausente en la BD")
                for r in registros if r["confirmada"] and r["clave"] not in en_bd]
    sin_confirmar = [(r["clave"], f"{r['op']} aplicada; k6 recibió {r['codigos']}")
                     for r in registros if not r["confirmada"] and r["codigo"] != 409
                     and r["clave"] in en_bd]
    falsos_409 = [(r["clave"], f"{r['op']} respondida 409 pero aplicada; k6 recibió {r['codigos']}")
                  for r in registros if r["codigo"] == 409 and r["clave"] in en_bd]
    sin_registro = [(c, f"{s['operacion']} en la BD sin registro en k6")
                    for c, s in en_bd.items() if c not in vistas]
    no_aplicadas = sum(1 for r in registros if not r["confirmada"] and r["codigo"] != 409
                       and r["clave"] not in en_bd)
    claves_repetidas = sum(n - 1 for n in Counter(r["clave"] for r in registros).values() if n > 1)

    rev.agregar("P1", perdidas)
    rev.agregar("D1", sin_crear)
    rev.dato("D2", exceso_r6)
    rev.dato("D", len(sin_crear) + exceso_r6)
    rev.agregar("S1", sin_confirmar)
    rev.agregar("S2", falsos_409)
    rev.agregar("S3", sin_registro)
    rev.dato("I1", len(registros))
    rev.dato("I2", sum(1 for r in registros if len(r["codigos"]) > 1))
    rev.dato("I3", no_aplicadas)
    rev.dato("I4", claves_repetidas)


def revisar(directorio):
    """Todas las revisiones sobre un directorio de resultados."""
    partidas, cartas, solicitudes = leer_bd(directorio)

    rev = Revision()
    rev.dato("N1", len(partidas))
    rev.dato("N2", sum(1 for p in partidas.values() if p["estado"] == TERMINADA))
    rev.dato("N3", sum(len(c) for c in cartas.values()))
    rev.dato("N4", len(solicitudes))
    exceso_r6 = verificar_reglas(partidas, cartas, solicitudes, rev)
    comparar_k6(directorio, partidas, solicitudes, exceso_r6, rev)
    return rev


def listar_revisiones():
    for d in REVISIONES:
        print(f"{d.id:<3} {d.grupo:<11} {d.gravedad:<9} {d.descripcion}")
        for caso in d.casos:
            print(f"      - {caso}")


def main():
    ap = argparse.ArgumentParser(description="Verificación de integridad de una corrida.")
    ap.add_argument("directorio", nargs="?", help="directorio de resultados, con bd/ dentro")
    ap.add_argument("--revisiones", action="store_true",
                    help="lista las revisiones, su gravedad y los casos que cubre cada una")
    args = ap.parse_args()
    if args.revisiones:
        return listar_revisiones()
    if not args.directorio:
        ap.error("falta el directorio")
    directorio = Path(args.directorio)
    rev = revisar(directorio)

    with open(directorio / "verificacion.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["grupo", "revision", "descripcion", "valor"])
        w.writerows(rev.filas)
    with open(directorio / "verificacion_detalle.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["revision", "referencia", "detalle"])
        w.writerows(rev.detalle)

    for grupo, clave, desc, valor in rev.filas:
        marca = "!!" if POR_ID[clave].gravedad != INFO and valor != 0 else "  "
        print(f"{marca} {clave:<3} {valor:>7}  {desc}")
    violaciones = sum(v for _, c, _, v in rev.filas if POR_ID[c].gravedad == VIOLACION)
    print(f"\nViolaciones de integridad (R1-R8, A1-A4, P1, D1): {violaciones}")


if __name__ == "__main__":
    main()
