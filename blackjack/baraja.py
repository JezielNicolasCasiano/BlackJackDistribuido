import random
import threading

PALOS = ["Corazones", "Diamantes", "Treboles", "Picas"]
VALORES = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


class Carta:
    def __init__(self, valor, palo):
        self.valor = valor
        self.palo = palo

    def puntos(self):
        """Valor de la carta. El As cuenta 1 aqui (el 11 se decide en calcular_total)."""
        if self.valor == "A":
            return 1
        if self.valor in ("J", "Q", "K"):
            return 10
        return int(self.valor)

    def __str__(self):
        return f"{self.valor} de {self.palo}"


def calcular_total(cartas):
    """Suma la mano. Un As vale 11 si con eso no se pasa de 21, si no vale 1."""
    total = sum(c.puntos() for c in cartas)
    hay_as = any(c.valor == "A" for c in cartas)
    if hay_as and total + 10 <= 21:
        total += 10
    return total


def texto_mano(cartas):
    return ", ".join(str(c) for c in cartas)


class Baraja:
    def __init__(self):
        self.cartas = []
        # Semaforo binario: solo un hilo a la vez puede sacar una carta
        self.semaforo = threading.Semaphore(1)

    def inicializar(self):
        """Crea las 52 cartas y las mezcla (algoritmo Fisher-Yates)."""
        self.cartas = [Carta(v, p) for p in PALOS for v in VALORES]
        for i in range(len(self.cartas) - 1, 0, -1):
            j = random.randint(0, i)
            self.cartas[i], self.cartas[j] = self.cartas[j], self.cartas[i]

    def tomar_carta(self):
        self.semaforo.acquire()          # entra a la seccion critica
        try:
            if len(self.cartas) == 0:    # por si se acaban con muchos jugadores
                self.inicializar()
            carta = self.cartas.pop()
        finally:
            self.semaforo.release()      # sale de la seccion critica
        return carta
