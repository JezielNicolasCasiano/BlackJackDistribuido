"""Modo de control (IDEMPOTENCIA=off), hipótesis H6: sin buscar la clave, un
reintento vuelve a aplicarse o recibe un 409 engañoso."""
import uuid

import pytest

import partidas

pytestmark = pytest.mark.usefixtures("bd_vacia")


@pytest.fixture(autouse=True)
def sin_idempotencia(monkeypatch):
    monkeypatch.setattr(partidas, "IDEMPOTENCIA", False)


def test_quien_reporta_el_modo(cliente):
    assert cliente.get("/quien").json()["idempotencia"] == "off"


def test_sin_clave_un_reintento_crea_otra_partida(crear, contar):
    """H6, primera mitad: crear dos veces con la misma clave duplica la partida."""
    clave = uuid.uuid4()
    a, b = crear(clave), crear(clave)
    assert (a.status_code, b.status_code) == (201, 201)
    assert a.json()["id"] != b.json()["id"]
    assert contar("partidas") == 2
    # La segunda no pudo guardar su clave (ON CONFLICT DO NOTHING): queda una
    # partida sin solicitud de creación, que es como se cuenta el duplicado
    assert contar("""partidas p WHERE NOT EXISTS
                     (SELECT 1 FROM solicitudes s WHERE s.partida_id = p.id AND s.operacion = 'crear')""") == 1


def test_sin_clave_un_reintento_de_pedir_recibe_409(partida_con, accion, contar):
    """H6, segunda mitad: la versión evita la carta duplicada, pero responde 409
    a una acción que sí se aplicó."""
    p = partida_con("2C", "10D", "3C", "7D", "4C")      # sin blackjack, para que siga en juego
    clave = uuid.uuid4()
    primera = accion(p, "pedir", clave=clave)
    reintento = accion(p, "pedir", clave=clave)
    assert (primera.status_code, reintento.status_code) == (200, 409)
    assert contar("cartas WHERE duenio = 'jugador'") == 3    # sin carta duplicada
