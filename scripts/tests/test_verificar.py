"""Pruebas del verificador: que no acuse nada en datos correctos y que cada
revisión detecte la falla que le corresponde.

No usan base de datos: arman partidas con las reglas del juego, las escriben
en el mismo formato que deja verificar.sh y luego plantan una falla a la vez.
"""
import csv
import gzip
import json
import random
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import verificar
from reglas import (calcular_resultado, calcular_total, crear_mazo, es_blackjack, jugar_crupier,
                    reparto_inicial, se_paso)

INICIO = datetime(2026, 11, 25, tzinfo=timezone.utc)


def jugar(n, rng):
    """n partidas completas, jugadas como el procesador con la estrategia conservador."""
    partidas, cartas, solicitudes, claves = [], [], [], []
    reloj = iter(INICIO + timedelta(milliseconds=i) for i in range(10**6))

    def accion(pid, op, codigo=200):
        clave = str(uuid.uuid4())
        solicitudes.append({"clave": clave, "partida_id": pid, "operacion": op,
                            "respuesta": "{}", "creada_en": str(next(reloj))})
        claves.append({"op": op, "clave": clave, "partida": pid, "codigos": [codigo],
                       "codigo": codigo, "confirmada": True})

    for _ in range(n):
        pid = str(uuid.uuid4())
        mazo = crear_mazo(rng)
        salidas = reparto_inicial(mazo)
        jugador = [c for d, c in salidas if d == "jugador"]
        crupier = [c for d, c in salidas if d == "crupier"]
        accion(pid, "crear", 201)
        if not es_blackjack(jugador):
            while calcular_total(jugador) < 15:
                carta = mazo[len(salidas)]
                jugador.append(carta)
                salidas.append(("jugador", carta))
                accion(pid, "pedir")
            if calcular_total(jugador) < 21:
                accion(pid, "plantarse")
            if not se_paso(jugador):
                for carta in jugar_crupier(crupier, mazo[len(salidas):]):
                    crupier.append(carta)
                    salidas.append(("crupier", carta))
        # Empieza en 1 (crear) y sube con cada acción: igual al número de solicitudes
        version = sum(1 for s in solicitudes if s["partida_id"] == pid)
        partidas.append({
            "id": pid, "jugador": "prueba", "estado": "terminada", "version": version,
            "mazo": "{" + ",".join(mazo) + "}", "pos_mazo": len(salidas),
            "resultado": calcular_resultado(jugador, crupier),
            "creada_en": str(INICIO), "terminada_en": str(INICIO),
        })
        cartas += [{"partida_id": pid, "orden": i, "duenio": d, "carta": c}
                   for i, (d, c) in enumerate(salidas, start=1)]
    return {"partidas": partidas, "cartas": cartas, "solicitudes": solicitudes, "claves": claves}


def escribir(directorio, datos):
    (directorio / "bd").mkdir(parents=True, exist_ok=True)
    for tabla in ("partidas", "cartas", "solicitudes"):
        filas = datos[tabla]
        with gzip.open(directorio / "bd" / f"{tabla}.csv.gz", "wt", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(filas[0].keys()))
            w.writeheader()
            w.writerows(filas)
    (directorio / "claves.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in datos["claves"]))


def valores(directorio):
    return {clave: valor for _, clave, _, valor in verificar.revisar(directorio).filas}


@pytest.fixture
def datos():
    return jugar(60, random.Random(7))


def partida_que(datos, condicion):
    """La primera partida (con sus cartas) que cumple la condición."""
    for p in datos["partidas"]:
        mano = [c for c in datos["cartas"] if c["partida_id"] == p["id"]]
        if condicion(p, mano):
            return p, mano
    raise AssertionError("ninguna partida cumple la condición")


def mano_de(mano, duenio):
    return [c["carta"] for c in mano if c["duenio"] == duenio]


# Las que deben dar 0 en datos correctos: todas menos las cifras (N, I) y D, que suma D1 y D2
REVISIONES = [d.id for d in verificar.REVISIONES
              if d.grupo != "datos" and d.id != "D" and not d.id.startswith("I")]


def test_datos_correctos_sin_violaciones(tmp_path, datos):
    escribir(tmp_path, datos)
    v = valores(tmp_path)
    assert {r: v[r] for r in REVISIONES} == {r: 0 for r in REVISIONES}
    assert v["N1"] == 60 and v["I1"] == len(datos["solicitudes"])
    assert v["I2"] == v["I3"] == v["I4"] == 0


def test_las_filas_salen_de_la_tabla(tmp_path, datos):
    escribir(tmp_path, datos)
    filas = verificar.revisar(tmp_path).filas
    assert [c for _, c, _, _ in filas] == verificar.IDS
    assert all(verificar.POR_ID[c].grupo == g and verificar.POR_ID[c].descripcion == d
               for g, c, d, _ in filas)


def test_sin_archivo_de_claves_solo_se_revisa_d1(tmp_path, datos):
    partida_duplicada(datos)
    escribir(tmp_path, datos)
    (tmp_path / "claves.jsonl").unlink()
    v = valores(tmp_path)
    assert v["D1"] == 1
    assert not {"P1", "S1", "I1", "D"} & v.keys()


def test_los_datos_cubren_los_casos(datos):
    """Las fallas de abajo necesitan partidas pasadas, plantadas y con crupier jugando."""
    partida_que(datos, lambda p, m: calcular_total(mano_de(m, "jugador")) > 21)
    partida_que(datos, lambda p, m: len(mano_de(m, "crupier")) > 2)
    assert any(s["operacion"] == "plantarse" for s in datos["solicitudes"])


# ---- Una falla a la vez: la revisión indicada debe acusarla ----

def carta_repetida(d):
    _, mano = partida_que(d, lambda p, m: len(m) >= 5)
    mano[4]["carta"] = mano[0]["carta"]


def sin_resultado(d):
    d["partidas"][0]["resultado"] = ""


def carta_invalida(d):
    d["cartas"][0]["carta"] = "11C"


def pos_mazo_de_mas(d):
    d["partidas"][0]["pos_mazo"] += 1


def carta_fuera_del_mazo(d):
    p, mano = partida_que(d, lambda p, m: True)
    mazo = p["mazo"].strip("{}").split(",")
    mano[0]["carta"], mano[2]["carta"] = mazo[2], mazo[0]   # el orden no coincide con el mazo


def pedir_sin_carta(d):
    p, _ = partida_que(d, lambda p, m: True)
    d["solicitudes"].append({"clave": str(uuid.uuid4()), "partida_id": p["id"],
                             "operacion": "pedir", "respuesta": "{}",
                             "creada_en": str(INICIO)})


def carta_tras_pasarse(d):
    """Un pedir aplicado dos veces sobre una partida que ya se había pasado."""
    p, mano = partida_que(d, lambda p, m: calcular_total(mano_de(m, "jugador")) > 21)
    mazo = p["mazo"].strip("{}").split(",")
    d["cartas"].append({"partida_id": p["id"], "orden": len(mano) + 1, "duenio": "jugador",
                        "carta": mazo[len(mano)]})
    p["pos_mazo"] += 1


def resultado_cambiado(d):
    p = d["partidas"][0]
    p["resultado"] = "empate" if p["resultado"] != "empate" else "ganada"


def sin_terminada_en(d):
    d["partidas"][0]["terminada_en"] = ""


def crupier_no_jugo(d):
    """Se borran las cartas que tomó el crupier: se quedó con menos de 17."""
    p, mano = partida_que(d, lambda p, m: len(mano_de(m, "crupier")) > 2)
    tomadas = [c for c in mano if c["duenio"] == "crupier"][2:]
    for c in tomadas:
        d["cartas"].remove(c)
    p["pos_mazo"] -= len(tomadas)


def sigue_en_juego(d):
    p = d["partidas"][0]
    p["estado"], p["resultado"], p["terminada_en"] = "en_juego", "", ""


def version_de_mas(d):
    d["partidas"][0]["version"] += 1


def confirmada_perdida(d):
    perdida = next(s for s in d["solicitudes"] if s["operacion"] == "plantarse")
    d["solicitudes"].remove(perdida)


def partida_duplicada(d):
    """Un crear aplicado dos veces sin idempotencia: la copia no tiene solicitud."""
    p = d["partidas"][0]
    copia = dict(p, id=str(uuid.uuid4()))
    d["partidas"].append(copia)
    d["cartas"] += [dict(c, partida_id=copia["id"])
                    for c in d["cartas"] if c["partida_id"] == p["id"]]


def aplicada_sin_respuesta(d):
    r = d["claves"][0]
    r.update(codigos=[504, 504, 504], codigo=504, confirmada=False)


def falso_409(d):
    r = next(r for r in d["claves"] if r["op"] == "pedir")
    r.update(codigos=[504, 409], codigo=409, confirmada=False)


def sin_registro_en_k6(d):
    d["claves"].pop()


def operacion_invalida(d):
    d["solicitudes"][0]["operacion"] = "doblar"


def con_reintento(d):
    d["claves"][0].update(codigos=[504, 201])


def fallida_sin_aplicar(d):
    """k6 agotó los reintentos y la acción nunca llegó a la BD: no es una pérdida."""
    r = next(r for r in d["claves"] if r["op"] == "pedir")
    r.update(codigos=[504, 504, 504], codigo=504, confirmada=False)
    d["solicitudes"] = [s for s in d["solicitudes"] if s["clave"] != r["clave"]]


def clave_repetida_en_k6(d):
    d["claves"].append(dict(d["claves"][0]))


@pytest.mark.parametrize("falla, revision", [
    (carta_repetida, "R1"),
    (sin_resultado, "R3"),
    (carta_invalida, "R4"),
    (pos_mazo_de_mas, "R5"),
    (carta_fuera_del_mazo, "R5"),
    (pedir_sin_carta, "R6"),
    (carta_tras_pasarse, "R7"),
    (carta_tras_pasarse, "D2"),
    (resultado_cambiado, "R8"),
    (sin_terminada_en, "A1"),
    (crupier_no_jugo, "A2"),
    (sigue_en_juego, "A3"),
    (version_de_mas, "A4"),
    (confirmada_perdida, "P1"),
    (partida_duplicada, "D1"),
    (aplicada_sin_respuesta, "S1"),
    (falso_409, "S2"),
    (sin_registro_en_k6, "S3"),
    (operacion_invalida, "R4"),
    (con_reintento, "I2"),
    (fallida_sin_aplicar, "I3"),
    (clave_repetida_en_k6, "I4"),
])
def test_detecta_la_falla(tmp_path, datos, falla, revision):
    falla(datos)
    escribir(tmp_path, datos)
    assert valores(tmp_path)[revision] >= 1


def test_fallida_sin_aplicar_no_es_perdida_ni_sin_confirmar(tmp_path, datos):
    fallida_sin_aplicar(datos)
    escribir(tmp_path, datos)
    v = valores(tmp_path)
    assert v["I3"] == 1 and v["P1"] == 0 and v["S1"] == 0
