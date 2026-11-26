"""GET, pedir y plantarse contra un PostgreSQL real. Se corren con scripts/probar.sh."""
import os
import threading
import uuid

import pytest

if "DB_HOST" not in os.environ:
    pytest.skip("sin base de datos: correr con scripts/probar.sh", allow_module_level=True)


import main  # noqa: E402
import partidas  # noqa: E402
from reglas import PALOS, VALORES  # noqa: E402

TODAS = [v + p for p in PALOS for v in VALORES]


@pytest.fixture(autouse=True)
def bd_vacia(cliente):
    with main.pool.connection() as conexion:
        conexion.execute("TRUNCATE partidas, cartas, solicitudes")


@pytest.fixture
def partida_con(cliente, monkeypatch):
    """Crea una partida con las primeras cartas del mazo elegidas a mano.

    El orden es el del reparto: jugador, crupier, jugador, crupier, y después
    las cartas que saldrán al pedir. El resto del mazo se completa solo.
    """
    def crear(*primeras):
        mazo = list(primeras) + [c for c in TODAS if c not in primeras]
        monkeypatch.setattr(partidas, "crear_mazo", lambda rng: mazo)
        r = cliente.post("/partidas", json={"jugador": "J1"},
                         headers={"Idempotency-Key": str(uuid.uuid4())})
        assert r.status_code == 201
        return r.json()
    return crear


def accion(cliente, p, operacion, version=None, clave=None):
    return cliente.post(f"/partidas/{p['id']}/{operacion}",
                        json={"version": version or p["version"]},
                        headers={"Idempotency-Key": str(clave or uuid.uuid4())})


def contar_cartas(dueno=None):
    with main.pool.connection() as conexion:
        sql = "SELECT count(*) FROM cartas" + (" WHERE duenio = %s" if dueno else "")
        return conexion.execute(sql, (dueno,) if dueno else ()).fetchone()[0]


# ---------------- Consultar ----------------

def test_consultar_devuelve_lo_mismo_que_al_crear(cliente, partida_con):
    p = partida_con("2C", "10D", "3C", "7D")
    r = cliente.get(f"/partidas/{p['id']}")
    assert r.status_code == 200 and r.json() == p
    assert "7D" not in str(r.json())              # la oculta tampoco viaja al consultar


def test_consultar_partida_que_no_existe(cliente):
    assert cliente.get(f"/partidas/{uuid.uuid4()}").status_code == 404
    assert cliente.get("/partidas/no-es-uuid").status_code == 422


# ---------------- Pedir ----------------

def test_pedir_agrega_una_carta_y_sube_la_version(cliente, partida_con):
    p = partida_con("2C", "10D", "3C", "7D", "4C")
    r = accion(cliente, p, "pedir")
    assert r.status_code == 200
    q = r.json()
    assert q["jugador"] == {"cartas": ["2C", "3C", "4C"], "total": 9}
    assert (q["version"], q["estado"]) == (2, "en_juego")
    assert q["crupier"]["cartas_visibles"] == ["10D"]


def test_pasarse_termina_sin_que_juegue_el_crupier(cliente, partida_con):
    p = partida_con("10C", "10D", "9C", "6D", "KC", "5D")
    q = accion(cliente, p, "pedir").json()
    assert (q["estado"], q["resultado"], q["jugador"]["total"]) == ("terminada", "perdida", 29)
    assert q["crupier"]["cartas_visibles"] == ["10D", "6D"]     # se descubre, pero no pide
    assert contar_cartas("crupier") == 2


def test_llegar_a_21_hace_jugar_al_crupier(cliente, partida_con):
    # Jugador 10+9 pide un 2 = 21. Crupier 10+6 = 16 pide un 3 = 19
    p = partida_con("10C", "10D", "9C", "6D", "2C", "3D")
    q = accion(cliente, p, "pedir").json()
    assert (q["estado"], q["resultado"]) == ("terminada", "ganada")
    assert q["crupier"] == {"cartas_visibles": ["10D", "6D", "3D"], "total_visible": 19}


# ---------------- Plantarse ----------------

def test_plantarse_hace_jugar_al_crupier(cliente, partida_con):
    # Jugador 18. Crupier 16 pide un 5 = 21
    p = partida_con("10C", "10D", "8C", "6D", "5D")
    q = accion(cliente, p, "plantarse").json()
    assert (q["estado"], q["resultado"], q["version"]) == ("terminada", "perdida", 2)
    assert q["crupier"]["total_visible"] == 21
    assert contar_cartas("jugador") == 2


def test_plantarse_empate(cliente, partida_con):
    p = partida_con("10C", "10D", "8C", "8D")
    q = accion(cliente, p, "plantarse").json()
    assert q["resultado"] == "empate"


def test_terminada_guarda_hora_de_fin_y_cartas_segun_el_mazo(cliente, partida_con):
    """R3 y R5 después de una partida completa con varios pedir."""
    p = partida_con("2C", "10D", "3C", "6D", "4C", "5C", "9D")
    p = accion(cliente, p, "pedir").json()
    p = accion(cliente, p, "pedir").json()
    accion(cliente, p, "plantarse")
    with main.pool.connection() as conexion:
        estado, fin, pos = conexion.execute(
            "SELECT estado, terminada_en IS NOT NULL, pos_mazo FROM partidas").fetchone()
        malas = conexion.execute("""SELECT count(*) FROM cartas c JOIN partidas p ON p.id = c.partida_id
                                    WHERE p.mazo[c.orden] <> c.carta""").fetchone()[0]
    assert (estado, fin, malas) == ("terminada", True, 0)
    assert pos == contar_cartas()


# ---------------- Versión ----------------

def test_version_vieja_es_409_con_el_estado_actual(cliente, partida_con):
    p = partida_con("2C", "10D", "3C", "7D", "4C", "5C")
    accion(cliente, p, "pedir")                          # la partida pasa a versión 2
    r = accion(cliente, p, "pedir", version=1)           # otra decisión, tomada viendo la versión 1
    assert r.status_code == 409
    assert r.json()["version"] == 2 and len(r.json()["jugador"]["cartas"]) == 3
    assert contar_cartas("jugador") == 3                 # no se aplicó nada


def test_actuar_en_partida_terminada_es_409(cliente, partida_con):
    p = partida_con("10C", "10D", "8C", "8D")
    q = accion(cliente, p, "plantarse").json()
    r = accion(cliente, q, "pedir")
    assert r.status_code == 409 and r.json()["estado"] == "terminada"


# ---------------- Idempotencia ----------------

def test_reintento_con_version_vieja_devuelve_lo_guardado(cliente, partida_con):
    """La clave se revisa antes que la versión.

    El reintento llega con la versión 1 porque el cliente nunca recibió la 2.
    Si se revisara primero la versión, respondería 409 a una acción que sí ocurrió.
    """
    p = partida_con("2C", "10D", "3C", "7D", "4C", "5C")
    clave = uuid.uuid4()
    primera = accion(cliente, p, "pedir", version=1, clave=clave)
    reintento = accion(cliente, p, "pedir", version=1, clave=clave)
    assert (primera.status_code, reintento.status_code) == (200, 200)
    assert reintento.json() == primera.json()
    assert contar_cartas("jugador") == 3                 # una sola carta nueva (R6)


def test_clave_de_otra_partida_es_422(cliente, partida_con):
    a = partida_con("2C", "10D", "3C", "7D")
    clave = uuid.uuid4()
    accion(cliente, a, "pedir", clave=clave)
    b = partida_con("2D", "10C", "3D", "7C")
    assert accion(cliente, b, "pedir", clave=clave).status_code == 422


def test_reintentos_simultaneos_de_pedir(cliente, partida_con):
    """5 copias del mismo pedir a la vez: el bloqueo las forma en fila, la
    primera aplica y las demás encuentran la clave."""
    p = partida_con("2C", "10D", "3C", "7D", "4C", "5C")
    clave, salida, resultados = uuid.uuid4(), threading.Barrier(5), []

    def intentar():
        salida.wait()
        resultados.append(partidas.pedir(main.pool, p["id"], clave, 1))

    hilos = [threading.Thread(target=intentar) for _ in range(5)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert [c for c, _ in resultados] == [200] * 5
    assert all(cuerpo == resultados[0][1] for _, cuerpo in resultados)
    assert contar_cartas("jugador") == 3


def test_dos_pedir_distintos_a_la_vez(cliente, partida_con):
    """El caso de E3 (concurrencia): dos decisiones distintas sobre la misma mano.

    El bloqueo hace esperar a la segunda, y la versión la rechaza con 409
    porque se decidió viendo una mano que ya cambió. Nunca un error 500.
    """
    p = partida_con("2C", "10D", "3C", "7D", "4C", "5C")
    salida, resultados = threading.Barrier(2), []

    def intentar():
        salida.wait()
        resultados.append(partidas.pedir(main.pool, p["id"], uuid.uuid4(), 1))

    hilos = [threading.Thread(target=intentar) for _ in range(2)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert sorted(c for c, _ in resultados) == [200, 409]
    assert contar_cartas("jugador") == 3


# ---------------- Solicitudes inválidas ----------------

def test_pedir_partida_que_no_existe_es_404(cliente):
    r = cliente.post(f"/partidas/{uuid.uuid4()}/pedir", json={"version": 1},
                     headers={"Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 404


@pytest.mark.parametrize("cuerpo", [{}, {"version": 0}, {"version": "uno"}])
def test_version_invalida_es_422(cliente, partida_con, cuerpo):
    p = partida_con("2C", "10D", "3C", "7D")
    r = cliente.post(f"/partidas/{p['id']}/pedir", json=cuerpo,
                     headers={"Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 422
