"""Procesador: esqueleto de infraestructura.

Contiene solo lo que necesita Kubernetes (sondas) y la conexión a la base de
datos. La lógica del juego (/partidas, /pedir, /plantarse) se agrega aquí.
Toda la configuración llega por variables de entorno (ConfigMap y Secret).
"""
import os
import socket
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from psycopg_pool import ConnectionPool

CONNINFO = (
    f"host={os.environ['DB_HOST']} port={os.environ.get('DB_PORT', '5432')} "
    f"dbname={os.environ['POSTGRES_DB']} user={os.environ['POSTGRES_USER']} "
    f"password={os.environ['POSTGRES_PASSWORD']}"
)
POOL_SIZE = int(os.environ.get("POOL_SIZE", "10"))
POD = socket.gethostname()  # nombre del pod; sirve para ver quién atendió

# Pool de conexiones (diseño, sección 8.3). open=False evita conectarse al
# importar el módulo, y check verifica cada conexión antes de prestarla,
# para descartar las que murieron si PostgreSQL se reinició.
pool = ConnectionPool(
    CONNINFO,
    min_size=1,
    max_size=POOL_SIZE,
    open=False,
    check=ConnectionPool.check_connection,
)


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    # wait=False: el procesador arranca aunque PostgreSQL todavía no responda
    # y el pool sigue intentando conectarse en segundo plano. Si fallar la
    # conexión matara al proceso, una caída de la BD tumbaría también a los
    # procesadores (falla en cascada, sección 8.2).
    pool.open(wait=False)
    yield
    pool.close()


app = FastAPI(title="Procesador", lifespan=ciclo_de_vida)


@app.get("/salud")
def salud():
    """Sonda de vida: ¿estoy roto yo? No revisa la base de datos."""
    return {"pod": POD}


@app.get("/listo")
def listo(respuesta: Response):
    """Sonda de disponibilidad: ¿puedo atender ahora? Sí revisa la base de datos."""
    try:
        with pool.connection(timeout=1) as conexion:
            conexion.execute("SELECT 1")
        return {"listo": True, "pod": POD}
    except Exception as error:
        respuesta.status_code = 503
        return {"listo": False, "pod": POD, "error": type(error).__name__}


@app.get("/quien")
def quien():
    """Ruta de diagnóstico: dice qué pod atendió la solicitud."""
    return {"pod": POD}
