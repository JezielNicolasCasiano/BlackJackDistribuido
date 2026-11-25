from baraja import Baraja, Carta, calcular_total


def evaluar_resultado(mano, mano_crupier):
    total = calcular_total(mano)
    total_c = calcular_total(mano_crupier)
    bj = len(mano) == 2 and total == 21
    bj_c = len(mano_crupier) == 2 and total_c == 21
    if total > 21:
        return "perdida", "PIERDE (se paso de 21)"
    if bj and not bj_c:
        return "ganada", "GANA con BLACKJACK"
    if bj_c and not bj:
        return "perdida", "PIERDE (el crupier tiene BLACKJACK)"
    if total_c > 21:
        return "ganada", "GANA (el crupier se paso)"
    if total > total_c:
        return "ganada", "GANA"
    if total < total_c:
        return "perdida", "PIERDE"
    return "empate", "EMPATA"


def mezclar():
    baraja = Baraja()
    baraja.inicializar()
    return [c.valor + c.palo[0] for c in reversed(baraja.cartas)]


def convertir(codigos):
    return [Carta(codigo[:-1], codigo[-1]) for codigo in codigos]


def total_codigos(codigos):
    return calcular_total(convertir(codigos))


def resultado_codigos(jugador, crupier):
    return evaluar_resultado(convertir(jugador), convertir(crupier))[0]
