"""Procesador: sondas y conexión a la BD. Aquí va la lógica del juego."""
import os
import socket
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse
from psycopg import errors
from psycopg_pool import ConnectionPool, PoolTimeout
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

import partidas

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

# La clave llega en un encabezado. Si falta o no es un UUID, FastAPI responde 422
Clave = Annotated[UUID, Header(alias="Idempotency-Key")]


# Validación estricta: un campo de más o una versión que no es entero ("1",
# true) son errores del cliente y se rechazan con 422 en lugar de adivinar
class NuevaPartida(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Sin espacios alrededor, para que "  " cuente como vacío
    jugador: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class Accion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(strict=True, ge=1)   # la versión que vio el jugador al decidir


@app.exception_handler(psycopg.OperationalError)
@app.exception_handler(PoolTimeout)
def sin_base_de_datos(solicitud: Request, error: Exception):
    """Sin conexión a la BD: 503, para que el cliente reintente con la misma clave."""
    return JSONResponse(status_code=503, content={"error": "sin base de datos", "pod": POD})


@app.exception_handler(errors.UndefinedTable)
def sin_esquema(solicitud: Request, error: Exception):
    """La BD responde pero las tablas no existen (aún no corre el Job del esquema): 503."""
    return JSONResponse(status_code=503, content={"error": "sin esquema", "pod": POD})


@app.exception_handler(partidas.NoExiste)
def no_existe(solicitud: Request, error: partidas.NoExiste):
    return JSONResponse(status_code=404, content={"error": "la partida no existe"})


@app.exception_handler(partidas.ClaveReutilizada)
def clave_reutilizada(solicitud: Request, error: partidas.ClaveReutilizada):
    """Error del cliente, no se arregla reintentando: 422."""
    return JSONResponse(status_code=422, content={"error": str(error)})


@app.get("/salud")
def salud():
    """Vida: no revisa la BD."""
    return {"pod": POD}


@app.get("/listo")
def listo(respuesta: Response):
    """Disponibilidad: revisa que la BD responda y que el esquema exista.

    Sin la tabla, cada operación fallaría; mejor no recibir tráfico.
    """
    try:
        with pool.connection(timeout=1) as conexion:
            conexion.execute("SELECT 1 FROM partidas LIMIT 1")
        return {"listo": True, "pod": POD}
    except Exception as error:
        respuesta.status_code = 503
        return {"listo": False, "pod": POD, "error": type(error).__name__}


@app.get("/quien")
def quien():
    """Diagnóstico: qué pod atendió y con qué modo de idempotencia."""
    return {"pod": POD, "idempotencia": "on" if partidas.IDEMPOTENCIA else "off"}


@app.post("/partidas")
def crear_partida(datos: NuevaPartida, clave: Clave, respuesta: Response):
    codigo, cuerpo = partidas.crear_partida(pool, clave, datos.jugador)
    respuesta.status_code = codigo
    return cuerpo


@app.get("/partidas/{id_partida}")
def consultar_partida(id_partida: UUID, respuesta: Response):
    codigo, cuerpo = partidas.consultar_partida(pool, id_partida)
    respuesta.status_code = codigo
    return cuerpo


@app.post("/partidas/{id_partida}/pedir")
def pedir(id_partida: UUID, datos: Accion, clave: Clave, respuesta: Response):
    codigo, cuerpo = partidas.pedir(pool, id_partida, clave, datos.version)
    respuesta.status_code = codigo
    return cuerpo


@app.post("/partidas/{id_partida}/plantarse")
def plantarse(id_partida: UUID, datos: Accion, clave: Clave, respuesta: Response):
    codigo, cuerpo = partidas.plantarse(pool, id_partida, clave, datos.version)
    respuesta.status_code = codigo
    return cuerpo
