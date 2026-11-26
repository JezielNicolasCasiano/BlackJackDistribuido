"""POST /partidas contra un PostgreSQL real. Se corren con scripts/probar.sh."""
import os
import random
import threading
import uuid

import pytest

if "DB_HOST" not in os.environ:
    pytest.skip("sin base de datos: correr con scripts/probar.sh", allow_module_level=True)

from psycopg_pool import ConnectionPool  # noqa: E402

import main  # noqa: E402
import partidas  # noqa: E402
from reglas import crear_mazo, es_blackjack, reparto_inicial  # noqa: E402


@pytest.fixture(autouse=True)
def bd_vacia(cliente):
    with main.pool.connection() as conexion:
        conexion.execute("TRUNCATE partidas, cartas, solicitudes")


def contar(tabla):
    with main.pool.connection() as conexion:
        return conexion.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0]


def crear(cliente, clave=None, jugador="J1"):
    return cliente.post("/partidas", json={"jugador": jugador},
                        headers={"Idempotency-Key": str(clave or uuid.uuid4())})


# ---------------- Crear ----------------

def test_crear_reparte_y_guarda_todo(cliente):
    r = crear(cliente)
    assert r.status_code == 201
    p = r.json()
    assert p["version"] == 1
    assert len(p["jugador"]["cartas"]) == 2
    if p["estado"] == "en_juego":
        assert len(p["crupier"]["cartas_visibles"]) == 1
    # Una partida, sus cuatro cartas y la solicitud que la creó
    assert (contar("partidas"), contar("cartas"), contar("solicitudes")) == (1, 4, 1)


def test_cartas_coinciden_con_el_mazo(cliente):
    """R5 (cartas según el mazo): mazo[orden] = carta y pos_mazo = cartas repartidas."""
    crear(cliente)
    with main.pool.connection() as conexion:
        malas = conexion.execute("""
            SELECT count(*) FROM cartas c JOIN partidas p ON p.id = c.partida_id
            WHERE p.mazo[c.orden] <> c.carta""").fetchone()[0]
        pos, n = conexion.execute("""
            SELECT p.pos_mazo, count(c.*) FROM partidas p JOIN cartas c ON c.partida_id = p.id
            GROUP BY p.id""").fetchone()
    assert malas == 0 and pos == n == 4


# ---------------- Idempotencia ----------------

def test_reintento_devuelve_lo_mismo_sin_crear_otra(cliente):
    clave = uuid.uuid4()
    primera = crear(cliente, clave)
    reintento = crear(cliente, clave)
    assert (primera.status_code, reintento.status_code) == (201, 200)
    assert reintento.json() == primera.json()
    assert contar("partidas") == 1


def test_clave_nueva_crea_otra_partida(cliente):
    a, b = crear(cliente), crear(cliente)
    assert a.json()["id"] != b.json()["id"]
    assert contar("partidas") == 2


def test_reintentos_simultaneos_crean_una_sola(cliente):
    """10 solicitudes con la misma clave al mismo tiempo (reintento en paralelo).

    Ninguna encuentra la clave al empezar, todas intentan crear y la llave
    primaria de solicitudes deja pasar solo a una. Las demás reciben su respuesta.
    """
    clave = uuid.uuid4()
    salida = threading.Barrier(10)
    resultados = []

    def intentar():
        salida.wait()                               # que arranquen juntas
        resultados.append(partidas.crear_partida(main.pool, clave, "J1"))

    hilos = [threading.Thread(target=intentar) for _ in range(10)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    codigos = sorted(c for c, _ in resultados)
    assert codigos == [200] * 9 + [201]
    # Mismo contenido. Se comparan diccionarios y no texto: jsonb guarda las
    # llaves en otro orden, y en JSON el orden de las llaves no importa
    assert all(cuerpo == resultados[0][1] for _, cuerpo in resultados)
    assert (contar("partidas"), contar("solicitudes")) == (1, 1)


def test_clave_de_otra_operacion_es_422(cliente):
    r = crear(cliente)
    clave = uuid.uuid4()
    with main.pool.connection() as conexion:
        conexion.execute(
            "INSERT INTO solicitudes (clave, partida_id, operacion, respuesta) VALUES (%s, %s, 'pedir', '{}')",
            (clave, r.json()["id"]))
    assert crear(cliente, clave).status_code == 422


# ---------------- Blackjack al repartir ----------------

def semilla_con_blackjack():
    for semilla in range(10_000):
        mazo = crear_mazo(random.Random(semilla))
        if es_blackjack([c for d, c in reparto_inicial(mazo) if d == "jugador"]):
            return semilla
    raise AssertionError("ninguna semilla dio blackjack")


def test_blackjack_termina_la_partida_al_crearse(cliente):
    codigo, p = partidas.crear_partida(main.pool, uuid.uuid4(), "J1",
                                       rng=random.Random(semilla_con_blackjack()))
    assert codigo == 201
    assert p["estado"] == "terminada"
    assert p["resultado"] in ("ganada", "empate")             # empate si el crupier también
    assert len(p["crupier"]["cartas_visibles"]) == 2          # al terminar se ve todo
    assert contar("cartas") == 4                              # el crupier no jugó
    with main.pool.connection() as conexion:
        assert conexion.execute("SELECT terminada_en IS NOT NULL FROM partidas").fetchone()[0]


# ---------------- Solicitudes inválidas y fallos ----------------

@pytest.mark.parametrize("encabezados, cuerpo", [
    ({}, {"jugador": "J1"}),                                  # sin clave
    ({"Idempotency-Key": "abc"}, {"jugador": "J1"}),          # clave que no es UUID
    ({"Idempotency-Key": str(uuid.uuid4())}, {"jugador": ""}),   # jugador vacío
    ({"Idempotency-Key": str(uuid.uuid4())}, {"jugador": "   "}),  # solo espacios
    ({"Idempotency-Key": str(uuid.uuid4())}, {"jugador": "J1", "extra": 1}),  # campo de más
    ({"Idempotency-Key": str(uuid.uuid4())}, {}),             # sin jugador
])
def test_solicitud_mal_formada_es_422(cliente, encabezados, cuerpo):
    assert cliente.post("/partidas", json=cuerpo, headers=encabezados).status_code == 422
    assert contar("partidas") == 0


def test_sin_base_de_datos_es_503(cliente, monkeypatch):
    caido = ConnectionPool("host=127.0.0.1 port=1 dbname=x user=x connect_timeout=1",
                           min_size=1, timeout=1, open=False)
    caido.open(wait=False)
    monkeypatch.setattr(main, "pool", caido)
    try:
        assert crear(cliente).status_code == 503
    finally:
        caido.close()


# ---------------- Sin esquema ----------------

def test_sin_tablas_no_esta_listo_y_responde_503(cliente):
    """Si la BD responde pero la tabla no existe, el pod no recibe tráfico (/listo
    503) y una operación responde 503, no 500. Se renombra la tabla un momento."""
    with main.pool.connection() as conexion:
        conexion.execute("ALTER TABLE partidas RENAME TO partidas_oculta")
    try:
        assert cliente.get("/listo").status_code == 503
        r = crear(cliente)
        assert r.status_code == 503 and r.json()["error"] == "sin esquema"
    finally:
        with main.pool.connection() as conexion:
            conexion.execute("ALTER TABLE partidas_oculta RENAME TO partidas")
    assert cliente.get("/listo").status_code == 200


def test_jugador_sin_espacios_alrededor(cliente):
    r = crear(cliente, jugador="  J1  ")
    assert r.status_code == 201
    with main.pool.connection() as conexion:
        assert conexion.execute("SELECT jugador FROM partidas").fetchone()[0] == "J1"
