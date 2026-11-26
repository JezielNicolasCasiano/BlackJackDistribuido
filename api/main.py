"""API: punto de entrada, reenvía al procesador."""
import logging
import os
import socket
from time import perf_counter
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

PROCESADOR_URL = os.environ["PROCESADOR_URL"]
POD = socket.gethostname()

# Cadena de tiempos: el cliente espera 8 s, esta API 5 s y el procesador 3 s para
# obtener una conexión de la BD. Cada capa vence antes que la de afuera, así el
# cliente recibe un 503/504 de la API en lugar de un corte por su propio timeout.
# No es una garantía: la cola de hilos del procesador no tiene límite de espera y
# una acción puede aplicarse después del 504; por eso el cliente reintenta con la
# misma clave
TIMEOUT_S = float(os.environ.get("PROCESADOR_TIMEOUT_S", "5"))

# false abre una conexión por solicitud y reparte mejor la carga entre pods: con
# true el cliente reutiliza conexiones, kube-proxy reparte por conexión y una de
# tres réplicas casi no recibía trabajo. Se conserva el interruptor para repetir
# esa comparación
KEEPALIVE = os.environ.get("API_KEEPALIVE", "false").lower() == "true"


def crear_cliente(transporte=None):
    """Cliente HTTP hacia el procesador; las pruebas pasan un transporte simulado."""
    return httpx.Client(
        base_url=PROCESADOR_URL,
        timeout=TIMEOUT_S,
        transport=transporte,
        limits=httpx.Limits(max_keepalive_connections=None if KEEPALIVE else 0),
    )


cliente = crear_cliente()

app = FastAPI(title="API")

# Una línea por solicitud con su clave, código y duración. Reemplaza el registro
# de acceso de uvicorn (--no-access-log en el Dockerfile), que no trae la clave:
# con ella se puede seguir un reintento en los registros de E4 y E5
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

# Misma validación que el procesador: lo mal formado se rechaza aquí con 422
Clave = Annotated[UUID, Header(alias="Idempotency-Key")]


class NuevaPartida(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jugador: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class Accion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(strict=True, ge=1)


class FalloProcesador(Exception):
    """El procesador no dio una respuesta utilizable; lleva la respuesta de error de la API."""

    def __init__(self, codigo, mensaje):
        self.respuesta = JSONResponse(status_code=codigo, content={"error": mensaje, "pod": POD})


def llamar(metodo, ruta, clave=None, cuerpo=None):
    """Llama al procesador y devuelve (código, contenido). Traduce los fallos de transporte."""
    encabezados = {"Idempotency-Key": str(clave)} if clave else {}
    try:
        r = cliente.request(metodo, ruta, headers=encabezados, json=cuerpo)
    # ConnectTimeout es subclase de TimeoutException: va primero
    except (httpx.ConnectTimeout, httpx.ConnectError):
        # La solicitud no llegó al procesador: el cliente puede reintentar sin riesgo
        raise FalloProcesador(503, "sin procesador disponible")
    except httpx.TimeoutException:
        # El procesador pudo haber aplicado la acción: el cliente reintenta con la misma clave
        raise FalloProcesador(504, "el procesador no respondió a tiempo")
    except httpx.TransportError:
        raise FalloProcesador(503, "sin procesador disponible")
    try:
        return r.status_code, r.json()
    except ValueError:
        raise FalloProcesador(502, "respuesta inválida del procesador")


def reenviar(metodo, ruta, clave=None, cuerpo=None):
    """Pasa la solicitud al procesador y devuelve su respuesta tal cual."""
    codigo, contenido = llamar(metodo, ruta, clave, cuerpo)
    return JSONResponse(status_code=codigo, content=contenido)


@app.exception_handler(FalloProcesador)
def fallo_procesador(solicitud: Request, error: FalloProcesador):
    return error.respuesta


@app.exception_handler(RequestValidationError)
def solicitud_invalida(solicitud: Request, error: RequestValidationError):
    """422 con la misma forma de error que el procesador (clave, cuerpo o id mal formados)."""
    return JSONResponse(status_code=422, content={
        "error": "solicitud inválida", "pod": POD, "detalle": jsonable_encoder(error.errors())})


@app.get("/salud")
def salud():
    return {"pod": POD}


@app.get("/listo")
def listo():
    return {"listo": True, "pod": POD, "keepalive": KEEPALIVE}


@app.get("/quien")
def quien():
    """Diagnóstico: qué pods atendieron. Un error del procesador se pasa con su código."""
    codigo, contenido = llamar("GET", "/quien")
    if codigo >= 300:   # sin esto, esperar_cadena contaría como sana una cadena con error
        return JSONResponse(status_code=codigo, content=contenido)
    return {"api": POD, "procesador": contenido["pod"]}


@app.post("/partidas")
def crear_partida(datos: NuevaPartida, clave: Clave):
    return reenviar("POST", "/partidas", clave, datos.model_dump())


@app.get("/partidas/{id_partida}")
def consultar_partida(id_partida: UUID):
    return reenviar("GET", f"/partidas/{id_partida}")


@app.post("/partidas/{id_partida}/pedir")
def pedir(id_partida: UUID, datos: Accion, clave: Clave):
    return reenviar("POST", f"/partidas/{id_partida}/pedir", clave, datos.model_dump())


@app.post("/partidas/{id_partida}/plantarse")
def plantarse(id_partida: UUID, datos: Accion, clave: Clave):
    return reenviar("POST", f"/partidas/{id_partida}/plantarse", clave, datos.model_dump())
