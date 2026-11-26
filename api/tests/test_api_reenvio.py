"""API: reenvío al procesador, traducción de fallos, validación local, registro y /quien."""
import importlib.util
import json
import logging
import pathlib
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

RAIZ = pathlib.Path(__file__).resolve().parents[2]
CLAVE = str(uuid.uuid4())
ID = str(uuid.uuid4())
H = {"Idempotency-Key": CLAVE}


@pytest.fixture
def modulo(monkeypatch):
    monkeypatch.setenv("PROCESADOR_URL", "http://procesador")
    espec = importlib.util.spec_from_file_location("api_main", RAIZ / "api" / "main.py")
    m = importlib.util.module_from_spec(espec)
    espec.loader.exec_module(m)
    return m


@pytest.fixture
def api(modulo):
    """Devuelve (TestClient, lista de solicitudes recibidas, función para fijar el manejador)."""
    recibidas = []
    destino = {}

    def manejador(solicitud):
        recibidas.append(solicitud)
        return destino["f"](solicitud)

    def fijar(f):
        destino["f"] = f

    fijar(lambda s: httpx.Response(200, json={}))
    modulo.cliente = httpx.Client(transport=httpx.MockTransport(manejador),
                                  base_url="http://procesador")
    return TestClient(modulo.app), recibidas, fijar


@pytest.mark.parametrize("codigo,cuerpo", [
    (201, {"id": ID, "version": 1}),
    (200, {"id": ID, "version": 2}),
    (409, {"error": "versión vieja", "partida": {"id": ID, "version": 3}}),
    (404, {"error": "la partida no existe"}),
    (422, {"error": "x", "pod": "p", "detalle": []}),
    (503, {"error": "sin base de datos", "pod": "p"}),
])
def test_pasa_codigo_y_cuerpo_tal_cual(api, codigo, cuerpo):
    cliente, _, fijar = api
    fijar(lambda s: httpx.Response(codigo, json=cuerpo))
    r = cliente.post(f"/partidas/{ID}/pedir", headers=H, json={"version": 1})
    assert r.status_code == codigo and r.json() == cuerpo


def test_reenvia_clave_y_cuerpo_validado(api):
    cliente, recibidas, _ = api
    cliente.post("/partidas", headers=H, json={"jugador": "  Ana  "})
    s = recibidas[0]
    assert s.url.path == "/partidas"
    assert s.headers["Idempotency-Key"] == CLAVE
    assert json.loads(s.content) == {"jugador": "Ana"}


def test_consulta_no_envia_clave(api):
    cliente, recibidas, _ = api
    cliente.get(f"/partidas/{ID}")
    assert "Idempotency-Key" not in recibidas[0].headers


def _falla(error):
    def f(solicitud):
        raise error
    return f


@pytest.mark.parametrize("error,codigo,mensaje", [
    (httpx.ConnectError("x"), 503, "sin procesador disponible"),
    (httpx.ConnectTimeout("x"), 503, "sin procesador disponible"),
    (httpx.ReadTimeout("x"), 504, "el procesador no respondió a tiempo"),
    (httpx.WriteTimeout("x"), 504, "el procesador no respondió a tiempo"),
    (httpx.PoolTimeout("x"), 504, "el procesador no respondió a tiempo"),
    (httpx.RemoteProtocolError("x"), 503, "sin procesador disponible"),
])
def test_fallos_de_transporte(api, modulo, error, codigo, mensaje):
    cliente, _, fijar = api
    fijar(_falla(error))
    r = cliente.post("/partidas", headers=H, json={"jugador": "Ana"})
    assert r.status_code == codigo
    assert r.json() == {"error": mensaje, "pod": modulo.POD}


def test_cuerpo_no_json_es_502(api, modulo):
    cliente, _, fijar = api
    fijar(lambda s: httpx.Response(200, content=b"<html>"))
    r = cliente.get(f"/partidas/{ID}")
    assert r.status_code == 502
    assert r.json() == {"error": "respuesta inválida del procesador", "pod": modulo.POD}


@pytest.mark.parametrize("cabeceras,cuerpo", [
    ({}, {"jugador": "Ana"}),
    ({"Idempotency-Key": "no-es-uuid"}, {"jugador": "Ana"}),
    (H, {"jugador": "Ana", "extra": 1}),
    (H, {"jugador": ""}),
    (H, {"jugador": "  "}),
    (H, {"jugador": "a" * 51}),
    (H, {}),
])
def test_validacion_local_crear(api, modulo, cabeceras, cuerpo):
    cliente, recibidas, _ = api
    r = cliente.post("/partidas", headers=cabeceras, json=cuerpo)
    assert r.status_code == 422
    j = r.json()
    assert j["error"] and j["pod"] == modulo.POD and j["detalle"]
    assert recibidas == []


@pytest.mark.parametrize("cuerpo", [
    {"version": "1"}, {"version": True}, {"version": 0}, {"version": 1.0}, {}, {"version": 1, "x": 1},
])
def test_validacion_local_accion(api, cuerpo):
    cliente, recibidas, _ = api
    for accion in ("pedir", "plantarse"):
        r = cliente.post(f"/partidas/{ID}/{accion}", headers=H, json=cuerpo)
        assert r.status_code == 422 and "detalle" in r.json()
    assert recibidas == []


def test_validacion_local_clave_e_id(api):
    cliente, recibidas, _ = api
    assert cliente.post(f"/partidas/{ID}/pedir", json={"version": 1}).status_code == 422
    assert cliente.post(f"/partidas/{ID}/pedir", headers={"Idempotency-Key": "x"},
                        json={"version": 1}).status_code == 422
    assert cliente.get("/partidas/no-es-uuid").status_code == 422
    assert cliente.post("/partidas/no-es-uuid/pedir", headers=H, json={"version": 1}).status_code == 422
    assert recibidas == []


def test_registro_con_clave_y_codigo(api, caplog):
    cliente, _, fijar = api
    fijar(lambda s: httpx.Response(201, json={}))
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        cliente.post("/partidas", headers=H, json={"jugador": "Ana"})
    linea = [m for m in caplog.messages if "POST /partidas" in m]
    assert len(linea) == 1 and f"clave={CLAVE}" in linea[0] and "codigo=201" in linea[0]


def test_registro_ignora_sondas(api, caplog):
    cliente, _, _ = api
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        cliente.get("/salud")
        cliente.get("/listo")
    assert caplog.messages == []


def test_quien_exitoso(api, modulo):
    cliente, _, fijar = api
    fijar(lambda s: httpx.Response(200, json={"pod": "proc-1"}))
    r = cliente.get("/quien")
    assert r.status_code == 200 and r.json() == {"api": modulo.POD, "procesador": "proc-1"}


@pytest.mark.parametrize("error,codigo", [
    (httpx.ConnectError("x"), 503), (httpx.ReadTimeout("x"), 504)])
def test_quien_con_error(api, modulo, error, codigo):
    cliente, _, fijar = api
    fijar(_falla(error))
    r = cliente.get("/quien")
    assert r.status_code == codigo
    assert set(r.json()) == {"error", "pod"} and r.json()["pod"] == modulo.POD


def test_quien_pasa_el_error_del_procesador(api):
    cliente, _, fijar = api
    fijar(lambda s: httpx.Response(503, json={"error": "sin base de datos", "pod": "proc-1"}))
    r = cliente.get("/quien")
    assert r.status_code == 503 and r.json() == {"error": "sin base de datos", "pod": "proc-1"}


def test_quien_no_json(api):
    cliente, _, fijar = api
    fijar(lambda s: httpx.Response(200, content=b"x"))
    assert cliente.get("/quien").status_code == 502


def test_keepalive_apagado_por_omision(modulo):
    assert modulo.KEEPALIVE is False
    assert modulo.TIMEOUT_S == 5.0
