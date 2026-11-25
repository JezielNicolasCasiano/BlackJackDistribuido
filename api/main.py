"""API: esqueleto de infraestructura.

Punto de entrada del sistema. Valida y reenvía al procesador (diseño,
sección 4.2). Por ahora solo tiene las sondas y una ruta de diagnóstico que
recorre la cadena completa k6 -> API -> procesador.
"""
import os
import socket

import httpx
from fastapi import FastAPI, Response

PROCESADOR_URL = os.environ["PROCESADOR_URL"]  # http://procesador:8000 (nombre del Service)
POD = socket.gethostname()

# API_KEEPALIVE decide si las conexiones hacia el procesador se reutilizan.
# El Service elige un pod al abrir cada conexión, no en cada solicitud, por lo
# que reutilizarlas puede concentrar el tráfico en un solo pod (diseño, 4.1).
#   true  -> reutiliza conexiones abiertas (menos costo por solicitud)
#   false -> abre una conexión nueva en cada solicitud (reparto más parejo)
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
    """Ruta de diagnóstico: pregunta al procesador qué pod atendió."""
    try:
        r = cliente.get("/quien")
        return {"api": POD, "procesador": r.json()["pod"]}
    except httpx.TimeoutException:
        respuesta.status_code = 504  # el procesador no respondió a tiempo (sección 10.3)
        return {"api": POD, "error": "timeout"}
    except httpx.TransportError:
        respuesta.status_code = 503  # no hay procesador alcanzable
        return {"api": POD, "error": "sin procesador"}
