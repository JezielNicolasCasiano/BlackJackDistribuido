import socket
import threading
import time

from baraja import Baraja, calcular_total, texto_mano
from reglas import evaluar_resultado
from pantalla import mostrar

PAUSA = 0.4  # segundos entre acciones, solo para poder leer la salida


def enviar(archivo, texto):
    archivo.write(texto + "\n")
    archivo.flush()


class Crupier:
    def __init__(self, num_jugadores, num_juegos, puerto=5000):
        self.num_jugadores = num_jugadores
        self.num_juegos = num_juegos
        self.puerto = puerto
        self.baraja = Baraja()

        self.etiquetas = {}      # id -> nombre del jugador
        self.manos = {}          # id -> lista de cartas
        self.mano_crupier = []
        self.resultados = {}     # id -> (categoria, texto)
        self.marcador = {}       # etiqueta -> [ganadas, perdidas, empates]

        # ---- Semaforos (el de pantalla esta en pantalla.py) ----
        self.sem_inicio_juego = threading.Semaphore(0)       # crupier -> inicializador
        self.sem_baraja_lista = threading.Semaphore(0)       # inicializador -> crupier
        self.sem_reparto = threading.Semaphore(0)            # crupier -> jugadores (ya hay cartas)
        self.sem_turno_terminado = threading.Semaphore(0)    # jugadores -> crupier
        self.sem_resultados = threading.Semaphore(0)         # crupier -> jugadores (ya hay resultado)
        self.sem_resultado_enviado = threading.Semaphore(0)  # jugadores -> crupier

    def log(self, texto):
        mostrar(texto)   # usa el semaforo de pantalla compartido

    # ------------------------------------------------------------------
    def iniciar(self):
        servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        servidor.bind(("0.0.0.0", self.puerto))
        servidor.listen()
        self.log(f"[Crupier] Esperando {self.num_jugadores} jugadores en el puerto {self.puerto}...")

        hilos = []
        for id_jugador in range(1, self.num_jugadores + 1):
            conexion, _ = servidor.accept()
            archivo = conexion.makefile("rw", encoding="utf-8", newline="\n")
            saludo = archivo.readline().strip().split("|")   # HOLA|nombre|estrategia
            etiqueta = f"Jugador {id_jugador} ({saludo[1]} - {saludo[2]})"
            self.etiquetas[id_jugador] = etiqueta
            self.manos[id_jugador] = []
            self.marcador[etiqueta] = [0, 0, 0]
            self.log(f"[Crupier] {etiqueta} se unio a la mesa")
            hilo = threading.Thread(target=self.atender_jugador,
                                    args=(id_jugador, conexion, archivo))
            hilo.start()
            hilos.append(hilo)

        # Los jugadores que lleguen de mas reciben un aviso de "mesa llena"
        threading.Thread(target=self.rechazar_extras, args=(servidor,), daemon=True).start()

        hilos.append(threading.Thread(target=self.hilo_inicializador))
        hilos.append(threading.Thread(target=self.hilo_crupier))
        hilos[-2].start()
        hilos[-1].start()
        for h in hilos:
            h.join()
        servidor.close()

    def rechazar_extras(self, servidor):
        while True:
            try:
                conexion, _ = servidor.accept()
                conexion.sendall(b"MESA_LLENA\n")
                conexion.close()
                self.log("[Crupier] Se rechazo a un jugador de mas (mesa llena)")
            except OSError:
                break

    # ------------------------------------------------------------------
    def hilo_inicializador(self):
        """Hilo que prepara y mezcla la baraja antes de cada juego."""
        for _ in range(self.num_juegos):
            self.sem_inicio_juego.acquire()
            self.log("[Inicializador] Creando y mezclando la baraja de 52 cartas")
            self.baraja.inicializar()
            self.log("[Inicializador] Baraja lista")
            self.sem_baraja_lista.release()

    def hilo_crupier(self):
        """Hilo del crupier: controla el flujo de cada juego."""
        n = self.num_jugadores
        for juego in range(1, self.num_juegos + 1):
            self.log(f"\n========== JUEGO {juego} de {self.num_juegos} ==========")
            self.sem_inicio_juego.release()
            self.sem_baraja_lista.acquire()

            self.repartir()
            for _ in range(n):
                self.sem_reparto.release()          # los jugadores ya pueden jugar
            for _ in range(n):
                self.sem_turno_terminado.acquire()  # espera a que todos terminen

            self.jugar_crupier()
            self.calcular_resultados()
            for _ in range(n):
                self.sem_resultados.release()
            for _ in range(n):
                self.sem_resultado_enviado.acquire()
        self.mostrar_marcador()

    # ------------------------------------------------------------------
    def repartir(self):
        self.mano_crupier = []
        for id_jugador in self.manos:
            self.manos[id_jugador] = []
        for ronda in range(2):
            for id_jugador in self.manos:
                carta = self.baraja.tomar_carta()
                self.manos[id_jugador].append(carta)
                self.log(f"[Crupier] Reparte {carta} a {self.etiquetas[id_jugador]}")
                time.sleep(PAUSA)
            carta = self.baraja.tomar_carta()
            self.mano_crupier.append(carta)
            if ronda == 0:
                self.log(f"[Crupier] Se reparte su carta visible: {carta}")
            else:
                self.log("[Crupier] Se reparte su carta oculta")
            time.sleep(PAUSA)

    def jugar_crupier(self):
        self.log(f"[Crupier] Descubre su carta oculta. Mano: {texto_mano(self.mano_crupier)} "
                 f"(total {calcular_total(self.mano_crupier)})")
        while calcular_total(self.mano_crupier) < 17:
            time.sleep(PAUSA)
            carta = self.baraja.tomar_carta()
            self.mano_crupier.append(carta)
            self.log(f"[Crupier] Toma una carta: {carta} "
                     f"(total {calcular_total(self.mano_crupier)})")
        total = calcular_total(self.mano_crupier)
        if total > 21:
            self.log(f"[Crupier] Se pasa con {total}")
        else:
            self.log(f"[Crupier] Se planta con {total}")

    def calcular_resultados(self):
        total_c = calcular_total(self.mano_crupier)
        for id_jugador, mano in self.manos.items():
            total = calcular_total(mano)
            res = evaluar_resultado(mano, self.mano_crupier)
            self.resultados[id_jugador] = res
            etiqueta = self.etiquetas[id_jugador]
            posicion = {"ganada": 0, "perdida": 1, "empate": 2}[res[0]]
            self.marcador[etiqueta][posicion] += 1
            self.log(f"[Crupier] Resultado {etiqueta}: {total} vs {total_c} -> {res[1]}")

    def mostrar_marcador(self):
        self.log("\n========== MARCADOR FINAL ==========")
        for etiqueta, (g, p, e) in self.marcador.items():
            self.log(f"{etiqueta}: {g} ganadas, {p} perdidas, {e} empates")

    # ------------------------------------------------------------------
    def atender_jugador(self, id_jugador, conexion, archivo):
        """Un hilo por jugador conectado: maneja sus turnos con el protocolo de texto."""
        etiqueta = self.etiquetas[id_jugador]
        for _ in range(self.num_juegos):
            self.sem_reparto.acquire()   # espera a que el crupier reparta
            mano = self.manos[id_jugador]
            visible = self.mano_crupier[0].puntos()
            if visible == 1:
                visible = 11

            while calcular_total(mano) < 21:
                enviar(archivo, f"TURNO|{texto_mano(mano)}|{calcular_total(mano)}|{visible}")
                respuesta = archivo.readline().strip()
                if respuesta == "CARTA":
                    carta = self.baraja.tomar_carta()   # protegido con semaforo
                    mano.append(carta)
                    self.log(f"[{etiqueta}] Toma una carta: {carta} "
                             f"(total {calcular_total(mano)})")
                else:
                    self.log(f"[{etiqueta}] Se planta con {calcular_total(mano)}")
                    break

            total = calcular_total(mano)
            if total > 21:
                self.log(f"[{etiqueta}] Se pasa con {total}")
            elif total == 21:
                self.log(f"[{etiqueta}] Llego a 21")
            enviar(archivo, f"FIN_TURNO|{texto_mano(mano)}|{total}")
            self.sem_turno_terminado.release()

            self.sem_resultados.acquire()   # espera a que el crupier calcule
            texto = self.resultados[id_jugador][1]
            enviar(archivo, f"RESULTADO|{texto}|{texto_mano(self.mano_crupier)}"
                            f"|{calcular_total(self.mano_crupier)}")
            self.sem_resultado_enviado.release()

        enviar(archivo, "FIN_SESION")
        conexion.close()

