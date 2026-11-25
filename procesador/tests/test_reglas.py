"""Pruebas de las reglas del juego. Se corren sin clúster: pytest (desde la raíz del repo)."""
import random
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

from reglas import (EMPATE, GANADA, PERDIDA, calcular_resultado, calcular_total, crear_mazo,
                    es_blackjack, jugar_crupier, puntos, reparto_inicial, se_paso, turno_terminado)

FORMATO = re.compile(r"^(A|[2-9]|10|J|Q|K)[CDTP]$")   # el mismo de r4_carta_valida


# ---------------- Mazo ----------------

def test_mazo_tiene_52_cartas_distintas_y_validas():
    mazo = crear_mazo()
    assert len(mazo) == 52
    assert len(set(mazo)) == 52
    assert all(FORMATO.match(c) for c in mazo)


def test_misma_semilla_mismo_mazo():
    assert crear_mazo(random.Random(7)) == crear_mazo(random.Random(7))
    assert crear_mazo(random.Random(7)) != crear_mazo(random.Random(8))


def test_fisher_yates_reparte_parejo():
    """Cada carta debe caer en cada posición con la misma probabilidad (1/52).

    Atrapa el error clásico de elegir j entre todo el mazo (0..51) en lugar de
    entre las posiciones que faltan (0..i), que mezcla de forma sesgada.
    Con semilla fija el resultado es siempre el mismo, así que no falla al azar.
    """
    rng = random.Random(2026)
    veces = 52 * 400                      # se esperan 400 por posición
    conteo = Counter(crear_mazo(rng).index("AC") for _ in range(veces))
    assert set(conteo) == set(range(52))
    assert all(300 < n < 500 for n in conteo.values())


# ---------------- Puntos y totales ----------------

@pytest.mark.parametrize("carta, esperado", [("AC", 1), ("7D", 7), ("10P", 10), ("JT", 10), ("KC", 10)])
def test_puntos(carta, esperado):
    assert puntos(carta) == esperado


@pytest.mark.parametrize("cartas, total", [
    (["AC", "KT"], 21),          # As como 11
    (["AC", "AD"], 12),          # solo un As puede valer 11
    (["AC", "AD", "9C"], 21),
    (["AC", "5C", "KT"], 16),    # el As vuelve a valer 1 para no pasarse
    (["KT", "QC", "2D"], 22),
])
def test_calcular_total(cartas, total):
    assert calcular_total(cartas) == total


def test_blackjack_solo_con_dos_cartas():
    assert es_blackjack(["AC", "KT"])
    assert not es_blackjack(["7C", "7D", "7T"])   # 21 con tres cartas no es blackjack


def test_fin_de_turno():
    assert not turno_terminado(["10C", "9D"])
    assert turno_terminado(["10C", "9D", "2T"])   # llegar a 21 termina el turno
    assert se_paso(["10C", "9D", "5T"]) and turno_terminado(["10C", "9D", "5T"])


# ---------------- Reparto y crupier ----------------

def test_reparto_inicial_en_orden_del_primer_proyecto():
    mazo = ["2C", "3C", "4C", "5C", "6C"]
    assert reparto_inicial(mazo) == [("jugador", "2C"), ("crupier", "3C"),
                                     ("jugador", "4C"), ("crupier", "5C")]


@pytest.mark.parametrize("crupier, siguientes, nuevas", [
    (["10C", "6D"], ["5P", "9T"], ["5P"]),          # 16 pide, 21 se planta
    (["10C", "7D"], ["5P"], []),                    # 17 ya no pide
    (["AC", "6D"], ["5P"], []),                     # 17 con As como 11 tampoco
    (["10C", "2D"], ["3P", "4T", "KC"], ["3P", "4T"]),   # 12, 15, 19
    (["10C", "6D"], ["KC", "2D"], ["KC"]),          # se pasa con 26 y para
])
def test_jugar_crupier(crupier, siguientes, nuevas):
    assert jugar_crupier(crupier, siguientes) == nuevas


# ---------------- Resultado ----------------

@pytest.mark.parametrize("jugador, crupier, resultado", [
    (["10C", "9D", "5T"], ["10D", "6C", "KP"], PERDIDA),   # el jugador se pasa: pierde aunque el crupier también
    (["AC", "KT"], ["7C", "7D", "7T"], GANADA),            # blackjack contra 21 de tres cartas
    (["7C", "7D", "7T"], ["AD", "KC"], PERDIDA),           # 21 de tres cartas contra blackjack
    (["AC", "KT"], ["AD", "QC"], EMPATE),                  # ambos blackjack
    (["10C", "8D"], ["10D", "6C", "KP"], GANADA),          # el crupier se pasa
    (["10C", "9D"], ["10D", "8C"], GANADA),
    (["10C", "7D"], ["10D", "8C"], PERDIDA),
    (["10C", "8D"], ["9D", "9C"], EMPATE),
])
def test_calcular_resultado(jugador, crupier, resultado):
    assert calcular_resultado(jugador, crupier) == resultado


# ---------------- Comparación con el primer proyecto ----------------

@pytest.fixture(scope="module")
def primer_proyecto():
    """Importa baraja.py y crupier.py tal como se entregaron."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "blackjack"))
    import baraja
    import crupier
    return baraja, crupier


def a_objeto(baraja, carta):
    palos = {"C": "Corazones", "D": "Diamantes", "T": "Treboles", "P": "Picas"}
    return baraja.Carta(carta[:-1], palos[carta[-1]])


def test_mismos_resultados_que_el_primer_proyecto(primer_proyecto):
    """Miles de manos al azar: totales y resultados deben coincidir con el original."""
    baraja, crupier = primer_proyecto
    original = crupier.Crupier(num_jugadores=1, num_juegos=1)
    original.log = lambda texto: None            # sin imprimir
    original.etiquetas = {1: "J1"}
    original.marcador = {"J1": [0, 0, 0]}
    rng = random.Random(1)
    for _ in range(5000):
        mazo = crear_mazo(rng)
        jugador = mazo[:rng.randint(2, 5)]
        resto = mazo[len(jugador):]
        mano_c = resto[:2] + jugar_crupier(resto[:2], resto[2:])

        original.manos = {1: [a_objeto(baraja, c) for c in jugador]}
        original.mano_crupier = [a_objeto(baraja, c) for c in mano_c]
        original.calcular_resultados()

        assert calcular_total(jugador) == baraja.calcular_total(original.manos[1])
        assert calcular_resultado(jugador, mano_c) == original.resultados[1][0], (jugador, mano_c)
