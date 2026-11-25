"""Procesador: sondas y conexión a la BD. Aquí va la lógica del juego."""
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
POD = socket.gethostname()

# check descarta conexiones muertas si PostgreSQL se reinició
pool = ConnectionPool(
    CONNINFO,
    min_size=1,
    max_size=POOL_SIZE,
    open=False,
    check=ConnectionPool.check_connection,
)


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    # Arranca aunque la BD no responda (evita fallas en cascada)
    pool.open(wait=False)
    yield
    pool.close()


app = FastAPI(title="Procesador", lifespan=ciclo_de_vida)


@app.get("/salud")
def salud():
    """Vida: no revisa la BD."""
    return {"pod": POD}


@app.get("/listo")
def listo(respuesta: Response):
    """Disponibilidad: revisa la BD."""
    try:
        with pool.connection(timeout=1) as conexion:
            conexion.execute("SELECT 1")
        return {"listo": True, "pod": POD}
    except Exception as error:
        respuesta.status_code = 503
        return {"listo": False, "pod": POD, "error": type(error).__name__}


@app.get("/quien")
def quien():
    """Diagnóstico: qué pod atendió."""
    return {"pod": POD}
