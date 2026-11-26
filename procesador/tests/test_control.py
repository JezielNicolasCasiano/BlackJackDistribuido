"""Modo de control (IDEMPOTENCIA=off): lo que el diseño espera ver en H6."""
import os
import uuid

import pytest

if "DB_HOST" not in os.environ:
    pytest.skip("sin base de datos: correr con scripts/probar.sh", allow_module_level=True)

import main  # noqa: E402
import partidas  # noqa: E402
from reglas import PALOS, VALORES  # noqa: E402


@pytest.fixture(autouse=True)
def sin_idempotencia(cliente, monkeypatch):
    with main.pool.connection() as conexion:
        conexion.execute("TRUNCATE partidas, cartas, solicitudes")
    monkeypatch.setattr(partidas, "IDEMPOTENCIA", False)


def contar(sql):
    with main.pool.connection() as conexion:
        return conexion.execute(sql).fetchone()[0]


def crear(cliente, clave):
    return cliente.post("/partidas", json={"jugador": "J1"}, headers={"Idempotency-Key": str(clave)})


def test_quien_reporta_el_modo(cliente):
    assert cliente.get("/quien").json()["idempotencia"] == "off"


def test_sin_clave_un_reintento_crea_otra_partida(cliente):
    """H6, primera mitad: crear dos veces con la misma clave duplica la partida."""
    clave = uuid.uuid4()
    a, b = crear(cliente, clave), crear(cliente, clave)
    assert (a.status_code, b.status_code) == (201, 201)
    assert a.json()["id"] != b.json()["id"]
    assert contar("SELECT count(*) FROM partidas") == 2
    # La segunda no pudo guardar su clave (ON CONFLICT DO NOTHING): queda una
    # partida sin solicitud de creación, que es como se cuenta el duplicado
    assert contar("""SELECT count(*) FROM partidas p WHERE NOT EXISTS
                     (SELECT 1 FROM solicitudes s WHERE s.partida_id = p.id AND s.operacion = 'crear')""") == 1


def test_sin_clave_un_reintento_de_pedir_recibe_409(cliente, monkeypatch):
    """H6, segunda mitad: la versión evita la carta duplicada, pero responde 409
    a una acción que sí se aplicó."""
    primeras = ["2C", "10D", "3C", "7D", "4C"]          # sin blackjack, para que siga en juego
    mazo = primeras + [v + p for p in PALOS for v in VALORES if v + p not in primeras]
    monkeypatch.setattr(partidas, "crear_mazo", lambda rng: mazo)
    p = crear(cliente, uuid.uuid4()).json()
    clave = uuid.uuid4()
    cuerpo = {"version": p["version"]}
    url = f"/partidas/{p['id']}/pedir"
    primera = cliente.post(url, json=cuerpo, headers={"Idempotency-Key": str(clave)})
    reintento = cliente.post(url, json=cuerpo, headers={"Idempotency-Key": str(clave)})
    assert (primera.status_code, reintento.status_code) == (200, 409)
    assert contar("SELECT count(*) FROM cartas WHERE duenio = 'jugador'") == 3   # sin carta duplicada
