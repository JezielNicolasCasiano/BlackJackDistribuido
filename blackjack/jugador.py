import random
import socket
import threading
import time

from pantalla import mostrar



# ---- Estrategias: devuelven True si el jugador quiere pedir carta ----
def conservador(total, visible):
    return total < 15


def arriesgado(total, visible):
    return total < 19


def calculador(total, visible):
    # Si el crupier muestra carta baja (2-6) es probable que se pase: se juega seguro
    if visible <= 6:
        return total < 12
    return total < 17


def aleatorio(total, visible):
    if total < 12:
        return True
    if total >= 19:
        return False
    return random.random() < 0.5


ESTRATEGIAS = {
    "conservador": conservador,
    "arriesgado": arriesgado,
    "calculador": calculador,
    "aleatorio": aleatorio,
}


class Jugador(threading.Thread):
    def __init__(self, nombre, estrategia, host, puerto):
        super().__init__()
        self.nombre = nombre
        self.estrategia = estrategia
        self.decidir = ESTRATEGIAS[estrategia]
        self.host = host
        self.puerto = puerto

    def mostrar(self, texto):
        mostrar(f"[{self.nombre} | {self.estrategia}] {texto}")

    def conectar(self):
        # El crupier puede tardar en arrancar, por eso se reintenta
        for _ in range(30):
            try:
                return socket.create_connection((self.host, self.puerto))
            except OSError:
                time.sleep(1)
        return None

    def run(self):
        conexion = self.conectar()
        if conexion is None:
            self.mostrar("No se pudo conectar con el crupier")
            return
        try:
            self.jugar(conexion)
        except OSError:
            self.mostrar("Se perdio la conexion con el crupier. Ejecute docker compose down "
                         "y vuelva a iniciar")
        finally:
            conexion.close()

    def jugar(self, conexion):
        archivo = conexion.makefile("rw", encoding="utf-8", newline="\n")
        archivo.write(f"HOLA|{self.nombre}|{self.estrategia}\n")
        archivo.flush()
        self.mostrar("Conectado a la mesa")

        while True:
            linea = archivo.readline()
            if not linea:
                break
            partes = linea.strip().split("|")
            tipo = partes[0]

            if tipo == "TURNO":
                mano, total, visible = partes[1], int(partes[2]), int(partes[3])
                if self.decidir(total, visible):
                    accion = "CARTA"
                    self.mostrar(f"Mano: {mano} (total {total}), crupier muestra {visible} "
                                 f"-> TOMA UNA CARTA")
                else:
                    accion = "PLANTARSE"
                    self.mostrar(f"Mano: {mano} (total {total}), crupier muestra {visible} "
                                 f"-> SE PLANTA")
                archivo.write(accion + "\n")
                archivo.flush()

            elif tipo == "FIN_TURNO":
                self.mostrar(f"Termina su turno con {partes[2]} ({partes[1]})")

            elif tipo == "RESULTADO":
                self.mostrar(f"Resultado: {partes[1]} | Crupier: {partes[2]} (total {partes[3]})")

            elif tipo == "MESA_LLENA":
                self.mostrar("La mesa esta llena: hay mas jugadores de los que espera el crupier")
                break

            elif tipo == "FIN_SESION":
                self.mostrar("Fin de la sesion")
                break


def crear_jugadores(cantidad, host, puerto, prefijo="J", desplazamiento=0):
    """Crea los hilos de jugadores repartiendo las estrategias de forma ciclica.
    El desplazamiento permite que contenedores distintos arranquen con estrategias distintas."""
    nombres_estrategias = list(ESTRATEGIAS.keys())
    jugadores = []
    for i in range(cantidad):
        estrategia = nombres_estrategias[(i + desplazamiento) % len(nombres_estrategias)]
        jugadores.append(Jugador(f"{prefijo}{i + 1}", estrategia, host, puerto))
    return jugadores
