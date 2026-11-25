"""API: punto de entrada, reenvía al procesador."""
import os
import socket

import httpx
from fastapi import FastAPI, Response

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
