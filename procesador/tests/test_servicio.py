"""El servicio HTTP (main.py): sondas, forma de los errores, pool y registro.
Contra un PostgreSQL real. Se corren con scripts/probar.sh."""
import logging
import re
import uuid

import pytest
from psycopg_pool import ConnectionPool

pytestmark = pytest.mark.usefixtures("bd_vacia")


# ---------------- Sondas y diagnóstico ----------------

def test_salud_no_revisa_la_bd(cliente, procesador, monkeypatch):
    # Un pool que nunca se abrió: cualquier intento de usarlo fallaría
    monkeypatch.setattr(procesador, "pool", ConnectionPool("host=x", open=False))
    r = cliente.get("/salud")
    assert (r.status_code, r.json()) == (200, {"pod": procesador.POD})


def test_quien_reporta_el_pod_y_el_modo(cliente, procesador):
    assert cliente.get("/quien").json() == {"pod": procesador.POD, "idempotencia": "on"}


# ---------------- Forma de los errores ----------------

def test_no_existe_tiene_la_forma_comun(cliente, procesador):
    r = cliente.get(f"/partidas/{uuid.uuid4()}")
    assert (r.status_code, r.json()) == (404, {"error": "la partida no existe",
                                               "pod": procesador.POD})


def test_clave_reutilizada_tiene_la_forma_comun(crear, accion, procesador):
    clave = uuid.uuid4()
    p = crear(clave).json()
    r = accion(p, "pedir", clave=clave)
    assert (r.status_code, r.json()) == (422, {"error": "la clave ya se usó para 'crear'",
                                               "pod": procesador.POD})


ALGUNA = f"/partidas/{uuid.uuid4()}"


@pytest.mark.parametrize("metodo, ruta, encabezados, cuerpo, campo", [
    ("POST", "/partidas", {}, {"jugador": "J1"}, ["header", "Idempotency-Key"]),
    ("POST", "/partidas", {"Idempotency-Key": "abc"}, {"jugador": "J1"},
     ["header", "Idempotency-Key"]),
    ("POST", "/partidas", {"Idempotency-Key": str(uuid.uuid4())}, {"jugador": ""},
     ["body", "jugador"]),
    ("GET", "/partidas/no-es-uuid", {}, None, ["path", "id_partida"]),
    ("POST", f"{ALGUNA}/plantarse", {"Idempotency-Key": str(uuid.uuid4())}, {"version": "1"},
     ["body", "version"]),
])
def test_validacion_tiene_la_forma_comun(cliente, procesador, metodo, ruta, encabezados,
                                         cuerpo, campo):
    r = cliente.request(metodo, ruta, headers=encabezados, json=cuerpo)
    assert r.status_code == 422
    j = r.json()
    assert set(j) == {"error", "pod", "detalle"}
    assert (j["error"], j["pod"]) == ("solicitud inválida", procesador.POD)
    assert [d["loc"] for d in j["detalle"]] == [campo]      # señala el campo culpable


# ---------------- Sin base de datos ----------------

def test_sin_base_de_datos_es_503(crear, procesador, monkeypatch):
    caido = ConnectionPool("host=127.0.0.1 port=1 dbname=x user=x connect_timeout=1",
                           min_size=1, timeout=1, open=False)
    caido.open(wait=False)
    monkeypatch.setattr(procesador, "pool", caido)
    try:
        r = crear()
    finally:
        caido.close()
    assert (r.status_code, r.json()) == (503, {"error": "sin base de datos", "pod": procesador.POD})


def test_pool_agotado_es_503(crear, procesador, monkeypatch):
    """Sin conexiones libres, la solicitud espera el timeout del pool y se rinde
    con 503 en lugar de quedarse trabajando para un cliente que ya se fue."""
    assert procesador.pool.timeout == procesador.POOL_TIMEOUT_S
    chico = ConnectionPool(procesador.CONNINFO, min_size=1, max_size=1, timeout=0.2, open=False)
    chico.open(wait=True, timeout=10)
    monkeypatch.setattr(procesador, "pool", chico)
    try:
        with chico.connection():                    # la única conexión, ocupada
            r = crear()
    finally:
        chico.close()
    assert (r.status_code, r.json()) == (503, {"error": "sin base de datos", "pod": procesador.POD})


def test_sin_tablas_no_esta_listo_y_responde_503(cliente, pool, crear, procesador):
    """Si la BD responde pero la tabla no existe, el pod no recibe tráfico (/listo
    503) y una operación responde 503, no 500. Se renombra la tabla un momento."""
    with pool.connection() as conexion:
        conexion.execute("ALTER TABLE partidas RENAME TO partidas_oculta")
    try:
        assert cliente.get("/listo").status_code == 503
        r = crear()
        assert (r.status_code, r.json()) == (503, {"error": "sin esquema", "pod": procesador.POD})
    finally:
        with pool.connection() as conexion:
            conexion.execute("ALTER TABLE partidas_oculta RENAME TO partidas")
    assert cliente.get("/listo").status_code == 200


# ---------------- Registro ----------------

def test_registro_una_linea_por_solicitud_sin_sondas(cliente, crear, caplog):
    clave = uuid.uuid4()
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        cliente.get("/salud")
        cliente.get("/listo")
        crear(clave)
        cliente.get(ALGUNA)
    lineas = [r.getMessage() for r in caplog.records if r.name == "uvicorn.error"]
    assert len(lineas) == 2
    assert re.fullmatch(rf"POST /partidas clave={clave} codigo=201 ms=\d+\.\d", lineas[0])
    assert re.fullmatch(rf"GET {ALGUNA} clave=- codigo=404 ms=\d+\.\d", lineas[1])
