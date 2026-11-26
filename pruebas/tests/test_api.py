"""Pruebas de punta a punta contra el sistema desplegado: pasan por la API, el procesador
y PostgreSQL reales (secciones 6 y 10 del documento). Complementan procesador/tests, que
prueba el procesador solo y con una sola réplica.

Ejecutar desde la raíz del repo, con el entorno ya levantado (scripts/preparar_entorno.sh):
  BASE_URL=http://$(minikube ip):30080 python -m pytest pruebas/tests/test_api.py -v -c /dev/null
(-c /dev/null evita que pytest.ini limite las pruebas a procesador/tests)
Con 2 o 3 réplicas, define REPLICAS_ESPERADAS=N para comprobar también el reparto de carga.
Si BASE_URL no está definida, se omiten."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

BASE = os.getenv("BASE_URL")
pytestmark = pytest.mark.skipif(not BASE, reason="define BASE_URL")


def post(ruta, cuerpo, clave=None):
    h = {"Idempotency-Key": clave or str(uuid.uuid4())}
    return requests.post(BASE + ruta, json=cuerpo, headers=h, timeout=10)


def nueva_partida_en_juego():
    """Crea partidas hasta obtener una que no termine en blackjack inicial."""
    for _ in range(30):
        r = post("/partidas", {"jugador": "t-" + uuid.uuid4().hex[:8]})
        assert r.status_code in (200, 201)
        if r.json()["estado"] == "en_juego":
            return r.json()
    pytest.skip("no salió una partida en juego tras 30 intentos")


# ---------- crear / consultar ----------
def test_crear_devuelve_201_y_forma_correcta():
    r = post("/partidas", {"jugador": "t-forma"})
    assert r.status_code == 201
    e = r.json()
    assert {"id", "estado", "version", "jugador", "crupier", "resultado"} <= e.keys()
    assert e["version"] == 1
    assert len(e["jugador"]["cartas"]) == 2


def test_no_revela_carta_oculta_en_juego():
    e = nueva_partida_en_juego()
    assert len(e["crupier"]["cartas_visibles"]) == 1
    assert e["resultado"] is None
    g = requests.get(f"{BASE}/partidas/{e['id']}", timeout=10).json()
    assert len(g["crupier"]["cartas_visibles"]) == 1


def test_get_inexistente_404():
    assert requests.get(f"{BASE}/partidas/{uuid.uuid4()}", timeout=10).status_code == 404


def test_422_cuerpo_mal_formado():
    assert post("/partidas", {"nombre": "x"}).status_code == 422


def test_crear_con_misma_clave_devuelve_200_y_misma_partida():
    clave = str(uuid.uuid4())
    r1 = post("/partidas", {"jugador": "t-idem"}, clave)
    r2 = post("/partidas", {"jugador": "t-idem"}, clave)
    assert r1.status_code == 201 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]


# ---------- idempotencia / versión ----------
def test_pedir_reintento_con_misma_clave_no_saca_otra_carta():
    e = nueva_partida_en_juego()
    clave = str(uuid.uuid4())
    r1 = post(f"/partidas/{e['id']}/pedir", {"version": e["version"]}, clave)
    r2 = post(f"/partidas/{e['id']}/pedir", {"version": e["version"]}, clave)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json() == r2.json()
    assert len(r1.json()["jugador"]["cartas"]) == 3


def test_pedir_con_version_vieja_409_con_estado_actual():
    e = nueva_partida_en_juego()
    r1 = post(f"/partidas/{e['id']}/pedir", {"version": e["version"]})
    assert r1.status_code == 200
    if r1.json()["estado"] != "en_juego":
        pytest.skip("la partida terminó con el primer pedir")
    r2 = post(f"/partidas/{e['id']}/pedir", {"version": e["version"]})
    assert r2.status_code == 409
    assert r2.json()["version"] == r1.json()["version"]


def test_accion_sobre_partida_terminada_409():
    e = nueva_partida_en_juego()
    r = post(f"/partidas/{e['id']}/plantarse", {"version": e["version"]})
    assert r.status_code == 200 and r.json()["estado"] == "terminada"
    assert r.json()["resultado"] is not None
    r2 = post(f"/partidas/{e['id']}/pedir", {"version": r.json()["version"]})
    assert r2.status_code == 409


def test_pedir_partida_inexistente_404():
    assert post(f"/partidas/{uuid.uuid4()}/pedir", {"version": 1}).status_code == 404


def test_pedir_sin_idempotency_key_422():
    e = nueva_partida_en_juego()
    r = requests.post(f"{BASE}/partidas/{e['id']}/pedir", json={"version": e["version"]}, timeout=10)
    assert r.status_code == 422


# ---------- concurrencia (E3 en pequeño) ----------
def test_dos_pedir_simultaneos_solo_uno_se_aplica():
    e = nueva_partida_en_juego()
    cuerpo = {"version": e["version"]}
    with ThreadPoolExecutor(2) as ex:
        rs = list(ex.map(lambda _: post(f"/partidas/{e['id']}/pedir", cuerpo), range(2)))
    assert sorted(r.status_code for r in rs) == [200, 409]
    final = requests.get(f"{BASE}/partidas/{e['id']}", timeout=10).json()
    assert len(final["jugador"]["cartas"]) == 3
    assert final["version"] == e["version"] + 1


def test_muchos_pedir_simultaneos_no_repiten_cartas():
    e = nueva_partida_en_juego()
    cuerpo = {"version": e["version"]}
    with ThreadPoolExecutor(10) as ex:
        rs = list(ex.map(lambda _: post(f"/partidas/{e['id']}/pedir", cuerpo), range(10)))
    assert sum(r.status_code == 200 for r in rs) == 1
    cartas = requests.get(f"{BASE}/partidas/{e['id']}", timeout=10).json()["jugador"]["cartas"]
    assert len(cartas) == len(set(cartas))


# ---------- cosas propias del diseño ----------
def test_quien_atraviesa_la_cadena_api_procesador():
    r = requests.get(f"{BASE}/quien", timeout=10)
    assert r.status_code == 200
    assert {"api", "procesador"} <= r.json().keys()


def test_clave_que_no_es_uuid_422():
    r = requests.post(f"{BASE}/partidas", json={"jugador": "t-x"},
                      headers={"Idempotency-Key": "no-soy-un-uuid"}, timeout=10)
    assert r.status_code == 422


def test_reusar_la_clave_de_crear_en_pedir_422():
    """Una clave identifica UNA acción: reusarla en otra operación es error del cliente."""
    clave = str(uuid.uuid4())
    for _ in range(30):
        r = post("/partidas", {"jugador": "t-reuso"}, clave)
        if r.json()["estado"] == "en_juego":
            break
        clave = str(uuid.uuid4())
    else:
        pytest.skip("solo salieron blackjacks")
    e = r.json()
    r2 = post(f"/partidas/{e['id']}/pedir", {"version": e["version"]}, clave)
    assert r2.status_code == 422


def test_version_en_cero_422():
    e = nueva_partida_en_juego()
    assert post(f"/partidas/{e['id']}/pedir", {"version": 0}).status_code == 422


def test_el_estado_consultado_coincide_con_el_de_la_accion():
    e = nueva_partida_en_juego()
    r = post(f"/partidas/{e['id']}/pedir", {"version": e["version"]})
    g = requests.get(f"{BASE}/partidas/{e['id']}", timeout=10)
    assert g.json() == r.json()


@pytest.mark.skipif(not os.getenv("REPLICAS_ESPERADAS"), reason="define REPLICAS_ESPERADAS=N")
def test_las_solicitudes_se_reparten_entre_todas_las_replicas():
    """Riesgo de la sección 4.1: con keep-alive la API puede pegarse a un solo pod.
    Si falla, repetir con API_KEEPALIVE=false en k8s/10-config.yaml."""
    esperadas = int(os.environ["REPLICAS_ESPERADAS"])
    vistos = set()
    for _ in range(300):
        vistos.add(requests.get(f"{BASE}/quien", timeout=10).json()["procesador"])
    assert len(vistos) == esperadas, f"solo respondieron: {sorted(vistos)}"
