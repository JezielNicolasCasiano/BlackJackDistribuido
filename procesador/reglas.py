"""Reglas del blackjack, adaptadas del primer proyecto (blackjack/baraja.py y crupier.py).

Funciones puras: no tocan la base de datos ni la red, solo reciben cartas y
devuelven resultados. Por eso se prueban sin clúster (procesador/tests/).

Una carta es un texto con su valor y su palo, el mismo formato de la base de
datos y de la API: "7C", "10P", "KT" (C corazones, D diamantes, T tréboles,
P picas).
"""
import random
from typing import NamedTuple

PALOS = ["C", "D", "T", "P"]
VALORES = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

# Resultados posibles, los mismos nombres que usaba calcular_resultados
GANADA, PERDIDA, EMPATE = "ganada", "perdida", "empate"

# Estados de una partida, dueños de una carta y operaciones: los mismos textos
# que aceptan las restricciones r4_* de la base de datos
EN_JUEGO, TERMINADA = "en_juego", "terminada"
JUGADOR, CRUPIER = "jugador", "crupier"
CREAR, PEDIR, PLANTARSE = "crear", "pedir", "plantarse"


def crear_mazo(rng=random):
    """Las 52 cartas mezcladas con Fisher-Yates, igual que Baraja.inicializar().

    rng se puede reemplazar por random.Random(semilla) para que las pruebas
    obtengan siempre el mismo mazo.
    """
    mazo = [v + p for p in PALOS for v in VALORES]
    # Recorre de atrás hacia adelante e intercambia cada carta con una al azar
    # de las que aún no se fijan (posiciones 0..i)
    for i in range(len(mazo) - 1, 0, -1):
        j = rng.randint(0, i)
        mazo[i], mazo[j] = mazo[j], mazo[i]
    return mazo


def puntos(carta):
    """Valor de una carta. El As cuenta 1 aquí (el 11 se decide en calcular_total)."""
    valor = carta[:-1]
    if valor == "A":
        return 1
    if valor in ("J", "Q", "K"):
        return 10
    return int(valor)


def calcular_total(cartas):
    """Suma la mano. Un As vale 11 si con eso no se pasa de 21, si no vale 1."""
    total = sum(puntos(c) for c in cartas)
    hay_as = any(c[:-1] == "A" for c in cartas)
    if hay_as and total + 10 <= 21:
        total += 10
    return total


def es_blackjack(cartas):
    """21 con las dos primeras cartas."""
    return len(cartas) == 2 and calcular_total(cartas) == 21


def se_paso(cartas):
    return calcular_total(cartas) > 21


def turno_terminado(cartas):
    """El turno del jugador termina solo al pasarse o al llegar a 21."""
    return calcular_total(cartas) >= 21


def reparto_inicial(mazo):
    """Las 4 primeras cartas en el orden del primer proyecto.

    Jugador, crupier (visible), jugador, crupier (oculta). Devuelve pares
    (dueño, carta) en el orden en que salen del mazo, que es su columna orden.
    """
    return [(JUGADOR, mazo[0]), (CRUPIER, mazo[1]),
            (JUGADOR, mazo[2]), (CRUPIER, mazo[3])]


def jugar_crupier(crupier, siguientes):
    """El crupier pide mientras tenga menos de 17, como jugar_crupier().

    crupier son sus cartas actuales y siguientes, las cartas que quedan en el
    mazo en orden. Devuelve solo las cartas nuevas que tomó.
    """
    mano = list(crupier)
    nuevas = []
    for carta in siguientes:
        if calcular_total(mano) >= 17:
            break
        mano.append(carta)
        nuevas.append(carta)
    return nuevas


def calcular_resultado(jugador, crupier):
    """Resultado para el jugador, la misma lógica que calcular_resultados() para uno solo."""
    total_j = calcular_total(jugador)
    total_c = calcular_total(crupier)
    bj_j = es_blackjack(jugador)
    bj_c = es_blackjack(crupier)
    if total_j > 21:
        return PERDIDA
    if bj_j and not bj_c:
        return GANADA
    if bj_c and not bj_j:
        return PERDIDA
    if total_c > 21:
        return GANADA
    if total_j > total_c:
        return GANADA
    if total_j < total_c:
        return PERDIDA
    return EMPATE


class Jugada(NamedTuple):
    """Cómo queda una partida después de repartir o de una acción del jugador."""
    nuevas: list            # pares (dueño, carta) que salieron del mazo, en orden
    jugador: list           # la mano completa del jugador
    crupier: list           # la mano completa del crupier
    estado: str             # EN_JUEGO o TERMINADA
    resultado: str | None   # None mientras se juega

    @property
    def terminada(self):
        return self.estado == TERMINADA


def repartir(mazo):
    """El reparto de una partida nueva: las cuatro primeras cartas del mazo.

    Con blackjack del jugador la partida termina al crearse, sin que juegue
    el crupier.
    """
    reparto = reparto_inicial(mazo)
    jugador = [c for d, c in reparto if d == JUGADOR]
    crupier = [c for d, c in reparto if d == CRUPIER]
    if es_blackjack(jugador):
        return Jugada(reparto, jugador, crupier, TERMINADA, calcular_resultado(jugador, crupier))
    return Jugada(reparto, jugador, crupier, EN_JUEGO, None)


def aplicar(operacion, jugador, crupier, mazo, pos_mazo):
    """PEDIR o PLANTARSE sobre una partida en juego.

    jugador y crupier son las manos actuales y pos_mazo, cuántas cartas se han
    repartido. mazo es una lista de Python (empieza en 0), así que la siguiente
    carta es mazo[pos_mazo]; en SQL sería mazo[pos_mazo + 1]. No modifica las
    listas que recibe: las manos de la Jugada son copias.
    """
    jugador, crupier = list(jugador), list(crupier)
    nuevas = []
    if operacion == PEDIR:
        jugador.append(mazo[pos_mazo])
        nuevas.append((JUGADOR, mazo[pos_mazo]))

    # Pasarse termina la partida sin que juegue el crupier. Plantarse o llegar
    # a 21 terminan el turno, y entonces juega el crupier
    if se_paso(jugador):
        estado = TERMINADA
    elif operacion == PLANTARSE or turno_terminado(jugador):
        for carta in jugar_crupier(crupier, mazo[pos_mazo + len(nuevas):]):
            crupier.append(carta)
            nuevas.append((CRUPIER, carta))
        estado = TERMINADA
    else:
        estado = EN_JUEGO
    resultado = calcular_resultado(jugador, crupier) if estado == TERMINADA else None
    return Jugada(nuevas, jugador, crupier, estado, resultado)
