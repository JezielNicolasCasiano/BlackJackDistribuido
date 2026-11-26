"""POST /partidas contra un PostgreSQL real. Se corren con scripts/probar.sh."""
import random
import uuid

import pytest

import partidas
from reglas import crear_mazo, repartir

pytestmark = pytest.mark.usefixtures("bd_vacia")


# ---------------- Crear ----------------

def test_crear_reparte_y_guarda_todo(crear, contar):
    r = crear()
    assert r.status_code == 201
    p = r.json()
    assert p["version"] == 1
    assert len(p["jugador"]["cartas"]) == 2
    if p["estado"] == "en_juego":
        assert len(p["crupier"]["cartas_visibles"]) == 1
    # Una partida, sus cuatro cartas y la solicitud que la creó
    assert (contar("partidas"), contar("cartas"), contar("solicitudes")) == (1, 4, 1)


def test_cartas_coinciden_con_el_mazo(pool, crear):
    """R5 (cartas según el mazo): mazo[orden] = carta y pos_mazo = cartas repartidas."""
    crear()
    with pool.connection() as conexion:
        malas = conexion.execute("""
            SELECT count(*) FROM cartas c JOIN partidas p ON p.id = c.partida_id
            WHERE p.mazo[c.orden] <> c.carta""").fetchone()[0]
        pos, n = conexion.execute("""
            SELECT p.pos_mazo, count(c.*) FROM partidas p JOIN cartas c ON c.partida_id = p.id
            GROUP BY p.id""").fetchone()
    assert malas == 0 and pos == n == 4


# ---------------- Idempotencia ----------------

def test_reintento_devuelve_lo_mismo_sin_crear_otra(crear, contar):
    clave = uuid.uuid4()
    primera = crear(clave)
    reintento = crear(clave)
    assert (primera.status_code, reintento.status_code) == (201, 200)
    assert reintento.json() == primera.json()
    assert contar("partidas") == 1


def test_clave_nueva_crea_otra_partida(crear, contar):
    a, b = crear(), crear()
    assert a.json()["id"] != b.json()["id"]
    assert contar("partidas") == 2


def test_reintentos_simultaneos_crean_una_sola(pool, en_paralelo, contar):
    """10 solicitudes con la misma clave al mismo tiempo (reintento en paralelo).

    Ninguna encuentra la clave al empezar, todas intentan crear y la llave
    primaria de solicitudes deja pasar solo a una. Las demás reciben su respuesta.
    """
    clave = uuid.uuid4()
    resultados = en_paralelo(10, lambda _: partidas.crear_partida(pool, clave, "J1"))
    codigos = sorted(c for c, _ in resultados)
    assert codigos == [200] * 9 + [201]
    # Mismo contenido. Se comparan diccionarios y no texto: jsonb guarda las
    # llaves en otro orden, y en JSON el orden de las llaves no importa
    assert all(cuerpo == resultados[0][1] for _, cuerpo in resultados)
    assert (contar("partidas"), contar("solicitudes")) == (1, 1)


def test_clave_de_otra_operacion_es_422(pool, crear):
    r = crear()
    clave = uuid.uuid4()
    with pool.connection() as conexion:
        conexion.execute(
            "INSERT INTO solicitudes (clave, partida_id, operacion, respuesta) VALUES (%s, %s, 'pedir', '{}')",
            (clave, r.json()["id"]))
    r = crear(clave)
    assert r.status_code == 422 and r.json()["error"] == "la clave ya se usó para 'pedir'"


# ---------------- Blackjack al repartir ----------------

def semilla_con_blackjack():
    for semilla in range(10_000):
        if repartir(crear_mazo(random.Random(semilla))).estado == "terminada":
            return semilla
    raise AssertionError("ninguna semilla dio blackjack")


def test_blackjack_termina_la_partida_al_crearse(pool, contar):
    codigo, p = partidas.crear_partida(pool, uuid.uuid4(), "J1",
                                       rng=random.Random(semilla_con_blackjack()))
    assert codigo == 201
    assert p["estado"] == "terminada"
    assert p["resultado"] in ("ganada", "empate")             # empate si el crupier también
    assert len(p["crupier"]["cartas_visibles"]) == 2          # al terminar se ve todo
    assert contar("cartas") == 4                              # el crupier no jugó
    assert contar("partidas WHERE terminada_en IS NOT NULL") == 1


# ---------------- Solicitudes inválidas ----------------

@pytest.mark.parametrize("encabezados, cuerpo", [
    ({}, {"jugador": "J1"}),                                  # sin clave
    ({"Idempotency-Key": "abc"}, {"jugador": "J1"}),          # clave que no es UUID
    ({"Idempotency-Key": str(uuid.uuid4())}, {"jugador": ""}),   # jugador vacío
    ({"Idempotency-Key": str(uuid.uuid4())}, {"jugador": "   "}),  # solo espacios
    ({"Idempotency-Key": str(uuid.uuid4())}, {"jugador": "x" * 51}),  # más de 50 caracteres
    ({"Idempotency-Key": str(uuid.uuid4())}, {"jugador": "J1", "extra": 1}),  # campo de más
    ({"Idempotency-Key": str(uuid.uuid4())}, {}),             # sin jugador
])
def test_solicitud_mal_formada_es_422(cliente, contar, encabezados, cuerpo):
    assert cliente.post("/partidas", json=cuerpo, headers=encabezados).status_code == 422
    assert contar("partidas") == 0


def test_jugador_sin_espacios_alrededor(pool, crear):
    r = crear(jugador="  J1  ")
    assert r.status_code == 201
    with pool.connection() as conexion:
        assert conexion.execute("SELECT jugador FROM partidas").fetchone()[0] == "J1"


def test_jugador_de_50_caracteres_se_acepta(crear):
    assert crear(jugador="x" * 50).status_code == 201
