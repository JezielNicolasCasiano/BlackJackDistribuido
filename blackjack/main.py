import argparse
import os
import socket
import sys
import threading
import time

from crupier import Crupier
from jugador import crear_jugadores


class Main:
    """Punto de entrada. Modos:
         crupier   -> contenedor/proceso del crupier (servidor)
         jugadores -> contenedor/proceso con N hilos de jugadores (clientes)
         local     -> todo en un solo proceso (sin Docker). Es el modo por defecto
    """

    def __init__(self):
        self.args = self.leer_argumentos()

    def leer_argumentos(self):
        parser = argparse.ArgumentParser(description="Simulacion de Black Jack con hilos y semaforos")
        parser.add_argument("modo", nargs="?", default="local", choices=["crupier", "jugadores", "local"],
                            help="Si no se indica, se usa el modo local")
        parser.add_argument("--jugadores", type=int, default=int(os.getenv("NUM_JUGADORES", 0)),
                            help="Numero total de jugadores en la mesa")
        parser.add_argument("--juegos", type=int, default=int(os.getenv("NUM_JUEGOS", 0)),
                            help="Numero de juegos consecutivos")
        parser.add_argument("--hilos", type=int, default=int(os.getenv("NUM_HILOS", 1)),
                            help="Hilos de jugador dentro de este contenedor")
        parser.add_argument("--host", default=os.getenv("HOST_CRUPIER", "crupier"))
        parser.add_argument("--puerto", type=int, default=int(os.getenv("PUERTO", 5000)))
        return parser.parse_args()

    def pedir_numero(self, valor, texto, defecto, minimo, maximo):
        """Usa el valor recibido; si no hay, pregunta por teclado (si hay terminal)."""
        if valor <= 0:
            if sys.stdin.isatty():
                respuesta = input(f"{texto} [{defecto}]: ").strip()
                valor = int(respuesta) if respuesta else defecto
            else:
                valor = defecto
        return max(minimo, min(maximo, valor))

    def ejecutar(self):
        modo = self.args.modo
        if modo == "crupier":
            self.modo_crupier()
        elif modo == "jugadores":
            self.modo_jugadores()
        else:
            self.modo_local()

    def modo_crupier(self):
        jugadores = self.pedir_numero(self.args.jugadores, "Numero de jugadores (1-7)", 3, 1, 7)
        juegos = self.pedir_numero(self.args.juegos, "Numero de juegos", 3, 1, 100)
        Crupier(jugadores, juegos, self.args.puerto).iniciar()

    def modo_jugadores(self):
        # Cada contenedor tiene una IP distinta (172.x.x.N): se usa el ultimo numero para
        # darle un nombre y una estrategia de arranque diferente a cada contenedor
        try:
            numero = int(socket.gethostbyname(socket.gethostname()).split(".")[-1])
        except OSError:
            numero = 0
        hilos = crear_jugadores(self.args.hilos, self.args.host, self.args.puerto,
                                f"C{numero}-J", numero)
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

    def modo_local(self):
        jugadores = self.pedir_numero(self.args.jugadores, "Numero de jugadores (1-7)", 3, 1, 7)
        juegos = self.pedir_numero(self.args.juegos, "Numero de juegos", 3, 1, 100)
        crupier = Crupier(jugadores, juegos, self.args.puerto)
        hilo_crupier = threading.Thread(target=crupier.iniciar)
        hilo_crupier.start()
        time.sleep(1)
        hilos = crear_jugadores(jugadores, "localhost", self.args.puerto)
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        hilo_crupier.join()


if __name__ == "__main__":
    Main().ejecutar()
