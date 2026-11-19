import threading

# Semaforo binario compartido: evita que dos hilos mezclen sus mensajes en la salida
sem_pantalla = threading.Semaphore(1)


def mostrar(texto):
    sem_pantalla.acquire()
    print(texto, flush=True)
    sem_pantalla.release()
