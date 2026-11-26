"""Verificación de integridad después de una prueba (diseño, secciones 7.2 y 7.3).

Uso: python3 scripts/verificar.py DIRECTORIO

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
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "procesador"))
from reglas import calcular_resultado, calcular_total, es_blackjack  # noqa: E402

ESTADOS = {"en_juego", "terminada"}
RESULTADOS = {"ganada", "perdida", "empate"}
DUENIOS = {"jugador", "crupier"}
OPERACIONES = {"crear", "pedir", "plantarse"}
CARTA = re.compile(r"^(A|[2-9]|10|J|Q|K)[CDTP]$")


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

    def agregar(self, grupo, clave, descripcion, casos):
        """casos: lista de (referencia, detalle). El valor es cuántos hay."""
        self.filas.append((grupo, clave, descripcion, len(casos)))
        self.detalle.extend((clave, ref, det) for ref, det in casos)

    def dato(self, grupo, clave, descripcion, valor):
        """Una cifra informativa, sin detalle."""
        self.filas.append((grupo, clave, descripcion, valor))


def recorrer(cartas):
    """Repite la partida carta por carta con las reglas del juego.

    Devuelve las manos finales y las anomalías encontradas: cartas del jugador
    que no debieron salir (R7) y cartas del crupier fuera de lugar.
    """
    r7, crupier_mal = [], []
    duenios = [d for _, d, _ in cartas[:4]]
    if duenios != ["jugador", "crupier", "jugador", "crupier"]:
        r7.append(f"reparto inicial en otro orden: {duenios}")
    jugador = [c for _, d, c in cartas[:4] if d == "jugador"]
    crupier = [c for _, d, c in cartas[:4] if d == "crupier"]
    turno_terminado = es_blackjack(jugador)
    for orden, duenio, carta in cartas[4:]:
        if duenio == "jugador":
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
        if s["operacion"] != "crear":
            orden_acciones[s["partida_id"]].append((s["creada_en"], s["operacion"]))

    casos = defaultdict(list)
    exceso_r6 = 0
    for pid, p in partidas.items():
        mano = cartas.get(pid, [])
        ops = por_partida[pid]
        terminada = p["estado"] == "terminada"

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
        n_jugador = sum(1 for _, d, _ in mano if d == "jugador")
        if n_jugador != 2 + ops["pedir"]:
            casos["R6"].append((pid, f"{n_jugador} cartas del jugador y {ops['pedir']} pedir"))
            exceso_r6 += max(0, n_jugador - 2 - ops["pedir"])

        # R7. Sin cartas tras terminar (recorrido) y nada después de plantarse
        jugador, crupier, r7, crupier_mal = recorrer(mano)
        for d in r7:
            casos["R7"].append((pid, d))
        acciones = [op for _, op in sorted(orden_acciones[pid])]
        if acciones.count("plantarse") > 1:
            casos["R7"].append((pid, f"{acciones.count('plantarse')} plantarse aplicados"))
        elif "plantarse" in acciones and acciones[-1] != "plantarse":
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
        debe_terminar = bj or calcular_total(jugador) >= 21 or ops["plantarse"] > 0
        if terminada != debe_terminar:
            casos["A3"].append((pid, f"estado {p['estado']} con total {calcular_total(jugador)} "
                                     f"y {ops['plantarse']} plantarse"))
        # Cada acción aplicada sube la versión en 1
        if p["version"] != 1 + ops["pedir"] + ops["plantarse"]:
            casos["A4"].append((pid, f"versión {p['version']} con "
                                     f"{ops['pedir'] + ops['plantarse']} acciones"))

    # R2. Clave única (la llave primaria lo impide; se cuenta por completitud)
    claves = Counter(s["clave"] for s in solicitudes)
    casos["R2"] = [(c, f"{n} veces") for c, n in claves.items() if n > 1]
    malas = [(s["clave"], f"operación {s['operacion']}")
             for s in solicitudes if s["operacion"] not in OPERACIONES]
    casos["R4"] += malas

    descripciones = [
        ("R1", "Carta única: cartas repetidas en una partida"),
        ("R2", "Clave única: claves aplicadas más de una vez"),
        ("R3", "Fin con resultado: terminada sin resultado o al revés"),
        ("R4", "Valores válidos: estado, resultado, dueño, carta u operación inválidos"),
        ("R5", "Cartas según el mazo: no coinciden con mazo[orden] o con pos_mazo"),
        ("R6", "Cartas según las acciones: cartas del jugador distintas de 2 + pedir"),
        ("R7", "Sin cartas tras terminar: cartas o acciones después de 21, pasarse o plantarse"),
        ("R8", "Resultado correcto: distinto del que calcula calcular_resultado"),
    ]
    for clave, desc in descripciones:
        rev.agregar("reglas", clave, desc, casos[clave])
    adicionales = [
        ("A1", "terminada_en no coincide con el estado (R3 no lo vigila)"),
        ("A2", "Crupier que jugó cuando no debía, de más o de menos"),
        ("A3", "Estado que no corresponde a las cartas y acciones"),
        ("A4", "Versión distinta de 1 + acciones aplicadas"),
    ]
    for clave, desc in adicionales:
        rev.agregar("adicionales", clave, desc, casos[clave])
    return exceso_r6


def comparar_k6(directorio, partidas, solicitudes, exceso_r6, rev):
    """Lo que vio k6 frente a lo que guardó la base de datos (diseño, sección 7.3)."""
    con_crear = {s["partida_id"] for s in solicitudes if s["operacion"] == "crear"}
    sin_crear = [(pid, "partida sin solicitud de crear") for pid in partidas if pid not in con_crear]

    # jugar.sh lo comprime al terminar; recién salido de k6 está sin comprimir
    rutas = [r for r in (directorio / "claves.jsonl", directorio / "claves.jsonl.gz") if r.exists()]
    if not rutas:
        rev.agregar("k6", "D1", "Partidas creadas de más (sin solicitud de crear)", sin_crear)
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

    rev.agregar("k6", "P1", "Operaciones perdidas: k6 recibió 2xx y la clave no está en la BD", perdidas)
    rev.agregar("k6", "D1", "Partidas creadas de más (sin solicitud de crear)", sin_crear)
    rev.dato("k6", "D2", "Cartas del jugador de más (exceso de R6)", exceso_r6)
    rev.dato("k6", "D", "Operaciones duplicadas: D1 + D2", len(sin_crear) + exceso_r6)
    rev.agregar("k6", "S1", "Aplicadas sin confirmar: sin 2xx tras los reintentos, pero en la BD",
                sin_confirmar)
    rev.agregar("k6", "S2", "Respondidas 409 aunque se aplicaron (control, H6)", falsos_409)
    rev.agregar("k6", "S3", "Claves en la BD que k6 no registró (registro incompleto)", sin_registro)
    rev.dato("k6", "I1", "Acciones registradas por k6", len(registros))
    rev.dato("k6", "I2", "Acciones con reintento", sum(1 for r in registros if len(r["codigos"]) > 1))
    rev.dato("k6", "I3", "Fallidas y no aplicadas (sin efecto en la BD)", no_aplicadas)
    rev.dato("k6", "I4", "Claves repetidas en el registro de k6 (error del script)", claves_repetidas)


def revisar(directorio):
    """Todas las revisiones sobre un directorio de resultados."""
    partidas, cartas, solicitudes = leer_bd(directorio)

    rev = Revision()
    rev.dato("datos", "N1", "Partidas", len(partidas))
    rev.dato("datos", "N2", "Partidas terminadas",
             sum(1 for p in partidas.values() if p["estado"] == "terminada"))
    rev.dato("datos", "N3", "Cartas", sum(len(c) for c in cartas.values()))
    rev.dato("datos", "N4", "Solicitudes", len(solicitudes))
    exceso_r6 = verificar_reglas(partidas, cartas, solicitudes, rev)
    comparar_k6(directorio, partidas, solicitudes, exceso_r6, rev)
    return rev


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[2])
    directorio = Path(sys.argv[1])
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
        marca = "  " if grupo in ("datos",) or clave.startswith(("I", "S")) or valor == 0 else "!!"
        print(f"{marca} {clave:<3} {valor:>7}  {desc}")
    # D2 no se suma: son las mismas partidas de R6 contadas por carta
    violaciones = sum(v for g, c, _, v in rev.filas
                      if g in ("reglas", "adicionales") or c in ("P1", "D1"))
    print(f"\nViolaciones de integridad (R1-R8, A1-A4, P1, D1): {violaciones}")


if __name__ == "__main__":
    main()
