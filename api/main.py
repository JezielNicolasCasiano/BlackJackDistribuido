"""API: punto de entrada, reenvía al procesador."""
import logging
import os
import socket
from time import perf_counter
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

PROCESADOR_URL = os.environ["PROCESADOR_URL"]
POD = socket.gethostname()

# false abre una conexión por solicitud y reparte mejor la carga entre pods
KEEPALIVE = os.environ.get("API_KEEPALIVE", "true").lower() == "true"
cliente = httpx.Client(
    base_url=PROCESADOR_URL,
    timeout=5,
    limits=httpx.Limits(max_keepalive_connections=None if KEEPALIVE else 0),
)

app = FastAPI(title="API")

# Una línea por solicitud con su clave, código y duración. Reemplaza el registro
# de acceso de uvicorn (--no-access-log en el Dockerfile), que no trae la clave:
# con ella se puede seguir un reintento en los registros de E4 y E5
registro = logging.getLogger("uvicorn.error")


@app.middleware("http")
async def registrar_solicitud(solicitud: Request, siguiente):
    inicio = perf_counter()
    respuesta = await siguiente(solicitud)
    if solicitud.url.path not in ("/salud", "/listo"):   # las sondas solo harían ruido
        registro.info("%s %s clave=%s codigo=%s ms=%.1f",
                      solicitud.method, solicitud.url.path,
                      solicitud.headers.get("Idempotency-Key", "-"),
                      respuesta.status_code, (perf_counter() - inicio) * 1000)
    return respuesta

# Misma validación que el procesador: lo mal formado se rechaza aquí con 422
Clave = Annotated[UUID, Header(alias="Idempotency-Key")]


class NuevaPartida(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jugador: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class Accion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(strict=True, ge=1)


def reenviar(metodo, ruta, clave=None, cuerpo=None):
    """Pasa la solicitud al procesador y devuelve su respuesta tal cual."""
    encabezados = {"Idempotency-Key": str(clave)} if clave else {}
    try:
        r = cliente.request(metodo, ruta, headers=encabezados, json=cuerpo)
        return JSONResponse(status_code=r.status_code, content=r.json())
    except httpx.TimeoutException:
        # El procesador pudo haber aplicado la acción: el cliente reintenta con la misma clave
        return JSONResponse(status_code=504, content={"error": "el procesador no respondió a tiempo"})
    except httpx.TransportError:
        return JSONResponse(status_code=503, content={"error": "sin procesador disponible"})


@app.get("/salud")
def salud():
    return {"pod": POD}


@app.get("/listo")
def listo():
    return {"listo": True, "pod": POD, "keepalive": KEEPALIVE}


@app.get("/quien")
def quien(respuesta: Response):
    """Diagnóstico: qué pods atendieron."""
    try:
        r = cliente.get("/quien")
        return {"api": POD, "procesador": r.json()["pod"]}
    except httpx.TimeoutException:
        respuesta.status_code = 504
        return {"api": POD, "error": "timeout"}
    except httpx.TransportError:
        respuesta.status_code = 503
        return {"api": POD, "error": "sin procesador"}


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
