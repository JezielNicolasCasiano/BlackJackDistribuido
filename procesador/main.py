"""Procesador: el servicio HTTP que aplica las operaciones del juego.

Aquí van las sondas, el pool de conexiones a PostgreSQL, la validación de lo
que llega, el registro y la traducción de errores a códigos HTTP. La lógica
del juego vive en partidas.py (transacciones) y reglas.py (funciones puras).
"""
import logging
import os
import socket
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import FastAPI, Header, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
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
# Cuánto espera una solicitud por una conexión libre antes de rendirse con 503.
# Menos que los 5 s tras los que la API deja de esperar al procesador: con los
# 30 s que trae psycopg_pool, el procesador seguía trabajando en solicitudes
# que el cliente ya había abandonado. No acota la espera en la cola de hilos de
# uvicorn, así que una acción todavía puede confirmarse después de que la API
# respondió 504; ese caso lo cubre la clave de idempotencia
POOL_TIMEOUT_S = float(os.environ.get("POOL_TIMEOUT_S", "3"))
POD = socket.gethostname()

# check descarta conexiones muertas si PostgreSQL se reinició
pool = ConnectionPool(
    CONNINFO,
    min_size=1,
    max_size=POOL_SIZE,
    timeout=POOL_TIMEOUT_S,
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

# Una línea por solicitud con su clave, código y duración, igual que en la API.
# Reemplaza el registro de acceso de uvicorn (--no-access-log en el Dockerfile),
# que no trae la clave: con ella se sigue un reintento de un pod a otro
registro = logging.getLogger("uvicorn.error")


class RegistroSolicitudes:
    """Middleware ASGI puro que escribe la línea de cada solicitud.

    Se escribe como ASGI y no con @app.middleware("http") porque ese envoltorio
    de Starlette agregaba ~0.36 ms de CPU por solicitud, contra ~0.12 ms de esta
    forma (medido). Con el procesador limitado a 100m de CPU, esa diferencia
    se notaba en las solicitudes por segundo.
    """
    SIN_REGISTRO = {"/salud", "/listo"}   # las sondas solo harían ruido

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in self.SIN_REGISTRO:
            return await self.app(scope, receive, send)
        inicio = perf_counter()
        codigo = 500   # si la aplicación falla sin llegar a responder

        async def enviar(mensaje):
            nonlocal codigo
            if mensaje["type"] == "http.response.start":
                codigo = mensaje["status"]
            await send(mensaje)

        try:
            await self.app(scope, receive, enviar)
        finally:
            clave = next((valor.decode("latin-1") for nombre, valor in scope["headers"]
                          if nombre == b"idempotency-key"), "-")
            registro.info("%s %s clave=%s codigo=%s ms=%.1f", scope["method"], scope["path"],
                          clave, codigo, (perf_counter() - inicio) * 1000)


app.add_middleware(RegistroSolicitudes)


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


# ---------------- Errores ----------------
# Misma forma en la API y en el procesador: {"error", "pod"}. El 409 no pasa
# por aquí: su cuerpo es la partida actual, para que el jugador vuelva a decidir

def _error(codigo, mensaje, **extra):
    return JSONResponse(status_code=codigo, content={"error": mensaje, "pod": POD, **extra})


@app.exception_handler(RequestValidationError)
def solicitud_invalida(solicitud: Request, error: RequestValidationError):
    """Cuerpo, clave (ausente o que no es UUID) o id mal formados: 422 con el detalle."""
    return _error(422, "solicitud inválida", detalle=jsonable_encoder(error.errors()))


@app.exception_handler(psycopg.OperationalError)
@app.exception_handler(PoolTimeout)
def sin_base_de_datos(solicitud: Request, error: Exception):
    """Sin conexión a la BD o sin conexión libre a tiempo: 503, para que el
    cliente reintente con la misma clave."""
    return _error(503, "sin base de datos")


@app.exception_handler(errors.UndefinedTable)
def sin_esquema(solicitud: Request, error: Exception):
    """La BD responde pero las tablas no existen (aún no corre el Job del esquema): 503."""
    return _error(503, "sin esquema")


@app.exception_handler(partidas.NoExiste)
def no_existe(solicitud: Request, error: partidas.NoExiste):
    return _error(404, "la partida no existe")


@app.exception_handler(partidas.ClaveReutilizada)
def clave_reutilizada(solicitud: Request, error: partidas.ClaveReutilizada):
    """Error del cliente, no se arregla reintentando: 422."""
    return _error(422, str(error))


# ---------------- Rutas ----------------

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


def _responder(respuesta: Response, codigo, cuerpo):
    """Pasa el (código, cuerpo) que devuelve partidas.py a la respuesta HTTP."""
    respuesta.status_code = codigo
    return cuerpo


@app.post("/partidas")
def crear_partida(datos: NuevaPartida, clave: Clave, respuesta: Response):
    return _responder(respuesta, *partidas.crear_partida(pool, clave, datos.jugador))


@app.get("/partidas/{id_partida}")
def consultar_partida(id_partida: UUID, respuesta: Response):
    return _responder(respuesta, *partidas.consultar_partida(pool, id_partida))


@app.post("/partidas/{id_partida}/pedir")
def pedir(id_partida: UUID, datos: Accion, clave: Clave, respuesta: Response):
    return _responder(respuesta, *partidas.pedir(pool, id_partida, clave, datos.version))


@app.post("/partidas/{id_partida}/plantarse")
def plantarse(id_partida: UUID, datos: Accion, clave: Clave, respuesta: Response):
    return _responder(respuesta, *partidas.plantarse(pool, id_partida, clave, datos.version))
