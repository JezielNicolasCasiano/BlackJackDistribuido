import logging
import os
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import UUID

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from contrato import Accion, Clave, CrearPartida


logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app):
    async with httpx.AsyncClient(
        base_url=os.getenv("PROCESADOR_URL", "http://procesador:8000"),
        timeout=httpx.Timeout(5.0, connect=2.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=0),
    ) as cliente:
        app.state.cliente = cliente
        yield


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def registrar_solicitud(request: Request, call_next):
    inicio = perf_counter()
    respuesta = await call_next(request)
    logger.info(
        "%s %s clave=%r estado=%s duracion_ms=%.2f",
        request.method, request.url.path, request.headers.get("Idempotency-Key"),
        respuesta.status_code, (perf_counter() - inicio) * 1000,
    )
    return respuesta


async def reenviar(metodo, ruta, body=None, clave=None):
    opciones = {}
    if clave is not None:
        opciones = {"headers": {"Idempotency-Key": str(clave)}, "json": body.model_dump()}
    try:
        respuesta = await app.state.cliente.request(metodo, ruta, **opciones)
    except httpx.ConnectTimeout:
        return JSONResponse({"detail": "Procesador no disponible"}, status_code=503)
    except httpx.TimeoutException:
        return JSONResponse({"detail": "Tiempo de espera del procesador agotado"}, status_code=504)
    except httpx.RequestError:
        return JSONResponse({"detail": "Procesador no disponible"}, status_code=503)
    return Response(
        respuesta.content, status_code=respuesta.status_code, media_type="application/json"
    )


@app.get("/salud")
async def salud():
    return {"estado": "ok"}


@app.get("/listo")
async def listo():
    return await reenviar("GET", "/listo")


@app.post("/partidas", status_code=201)
async def crear(body: CrearPartida, clave: Clave):
    return await reenviar("POST", "/partidas", body, clave)


@app.get("/partidas/{id}")
async def consultar(id: UUID):
    return await reenviar("GET", f"/partidas/{id}")


@app.post("/partidas/{id}/pedir")
async def pedir(id: UUID, body: Accion, clave: Clave):
    return await reenviar("POST", f"/partidas/{id}/pedir", body, clave)


@app.post("/partidas/{id}/plantarse")
async def plantarse(id: UUID, body: Accion, clave: Clave):
    return await reenviar("POST", f"/partidas/{id}/plantarse", body, clave)
