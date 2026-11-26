"""Pruebas de las reglas del juego. Se corren sin clúster: pytest (desde la raíz del repo)."""
import random
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

from reglas import (CRUPIER, EMPATE, EN_JUEGO, GANADA, JUGADOR, PEDIR, PERDIDA, PLANTARSE,
                    TERMINADA, aplicar, calcular_resultado, calcular_total, crear_mazo,
                    es_blackjack, jugar_crupier, puntos, repartir, reparto_inicial, se_paso,
                    turno_terminado)

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


# ---------------- repartir y aplicar ----------------

def test_repartir_sin_blackjack_queda_en_juego():
    j = repartir(["2C", "10D", "3C", "7D", "4C"])
    assert j.nuevas == reparto_inicial(["2C", "10D", "3C", "7D"])
    assert (j.jugador, j.crupier) == (["2C", "3C"], ["10D", "7D"])
    assert (j.estado, j.resultado, j.terminada) == (EN_JUEGO, None, False)


@pytest.mark.parametrize("mazo, resultado", [
    (["AC", "10D", "KC", "6D", "5P"], GANADA),     # el crupier tenía 16 y no pide
    (["AC", "AD", "KC", "QD"], EMPATE),            # los dos con blackjack
])
def test_repartir_con_blackjack_termina_sin_que_juegue_el_crupier(mazo, resultado):
    j = repartir(mazo)
    assert (j.estado, j.resultado, j.terminada) == (TERMINADA, resultado, True)
    assert len(j.nuevas) == 4 and len(j.crupier) == 2


# Las cuatro del reparto y después las que quedan en el mazo
REPARTO = ["10C", "10D", "9C", "6D"]


@pytest.mark.parametrize("operacion, siguientes, nuevas, estado, resultado", [
    # Jugador 19 pide un 2 = 21: termina su turno y el crupier (16) pide un 3 = 19
    (PEDIR, ["2C", "3D"], [(JUGADOR, "2C"), (CRUPIER, "3D")], TERMINADA, GANADA),
    # Pide un 5 = 24: se pasa y el crupier no juega aunque tenga 16
    (PEDIR, ["5C", "3D"], [(JUGADOR, "5C")], TERMINADA, PERDIDA),
    # Plantarse con 19: el crupier pide un 5 = 21
    (PLANTARSE, ["5C", "3D"], [(CRUPIER, "5C")], TERMINADA, PERDIDA),
])
def test_aplicar(operacion, siguientes, nuevas, estado, resultado):
    j = aplicar(operacion, ["10C", "9C"], ["10D", "6D"], REPARTO + siguientes, 4)
    assert j.nuevas == nuevas
    assert (j.estado, j.resultado) == (estado, resultado)
    assert j.jugador == ["10C", "9C"] + [c for d, c in nuevas if d == JUGADOR]
    assert j.crupier == ["10D", "6D"] + [c for d, c in nuevas if d == CRUPIER]


def test_aplicar_pedir_sin_terminar_sigue_en_juego():
    j = aplicar(PEDIR, ["2C", "3C"], ["10D", "7D"], ["2C", "10D", "3C", "7D", "4C", "5C"], 4)
    assert (j.nuevas, j.estado, j.resultado) == ([(JUGADOR, "4C")], EN_JUEGO, None)


def test_aplicar_toma_la_carta_de_pos_mazo():
    """pos_mazo cuenta las cartas ya repartidas: la siguiente es mazo[pos_mazo]."""
    mazo = ["2C", "10D", "3C", "7D", "4C", "5C", "6C"]
    j = aplicar(PEDIR, ["2C", "3C", "4C"], ["10D", "7D"], mazo, 5)
    assert j.nuevas == [(JUGADOR, "5C")]


def test_plantarse_con_17_o_mas_no_saca_cartas():
    j = aplicar(PLANTARSE, ["10C", "8C"], ["10D", "8D"], ["10C", "10D", "8C", "8D", "2C"], 4)
    assert (j.nuevas, j.estado, j.resultado) == ([], TERMINADA, EMPATE)


def test_aplicar_no_modifica_lo_que_recibe():
    jugador, crupier = ["10C", "9C"], ["10D", "6D"]
    mazo = REPARTO + ["2C", "3D"]
    copia = (list(jugador), list(crupier), list(mazo))
    aplicar(PEDIR, jugador, crupier, mazo, 4)
    assert (jugador, crupier, mazo) == copia


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
    """Importa baraja.py y crupier.py tal como se entregaron.

    blackjack/ tiene su propio main.py: la carpeta se agrega a sys.path solo
    mientras corren estas pruebas y al final se olvidan sus módulos, para que
    un import main posterior no traiga el del primer proyecto.
    """
    carpeta = Path(__file__).resolve().parents[2] / "blackjack"
    with pytest.MonkeyPatch.context() as mp:
        mp.syspath_prepend(str(carpeta))
        import baraja
        import crupier
        yield baraja, crupier
    for nombre, modulo in list(sys.modules.items()):
        if Path(getattr(modulo, "__file__", None) or "/").parent == carpeta:
            del sys.modules[nombre]


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
