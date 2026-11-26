"""GET, pedir y plantarse contra un PostgreSQL real. Se corren con scripts/probar.sh."""
import threading
import uuid

import pytest

import partidas

pytestmark = pytest.mark.usefixtures("bd_vacia")


# ---------------- Consultar ----------------

def test_consultar_devuelve_lo_mismo_que_al_crear(cliente, partida_con):
    p = partida_con("2C", "10D", "3C", "7D")
    r = cliente.get(f"/partidas/{p['id']}")
    assert r.status_code == 200 and r.json() == p
    assert "7D" not in str(r.json())              # la oculta tampoco viaja al consultar


def test_consultar_ve_un_solo_estado(cliente, pool, partida_con, monkeypatch):
    """Un pedir confirmado entre las dos lecturas de la consulta no se mezcla.

    Se fuerza la carrera: justo antes de leer las cartas, otra conexión pide y
    confirma. La consulta debe responder el estado anterior completo (versión 1
    y dos cartas), no la versión 1 con la carta nueva.
    """
    p = partida_con("2C", "10D", "3C", "7D", "4C")
    original = partidas._cartas

    def pedir_en_medio(conexion, id_partida):
        monkeypatch.setattr(partidas, "_cartas", original)   # solo la primera vez
        codigo, _ = partidas.pedir(pool, id_partida, uuid.uuid4(), 1)
        assert codigo == 200
        return original(conexion, id_partida)

    monkeypatch.setattr(partidas, "_cartas", pedir_en_medio)
    r = cliente.get(f"/partidas/{p['id']}")
    assert r.status_code == 200 and r.json() == p
    # Y la siguiente consulta ya ve el pedir completo
    despues = cliente.get(f"/partidas/{p['id']}").json()
    assert despues["version"] == 2 and despues["jugador"]["cartas"] == ["2C", "3C", "4C"]


def test_consultar_partida_que_no_existe(cliente):
    assert cliente.get(f"/partidas/{uuid.uuid4()}").status_code == 404
    assert cliente.get("/partidas/no-es-uuid").status_code == 422


# ---------------- Pedir ----------------

def test_pedir_agrega_una_carta_y_sube_la_version(partida_con, accion):
    p = partida_con("2C", "10D", "3C", "7D", "4C")
    r = accion(p, "pedir")
    assert r.status_code == 200
    q = r.json()
    assert q["jugador"] == {"cartas": ["2C", "3C", "4C"], "total": 9}
    assert (q["version"], q["estado"]) == (2, "en_juego")
    assert q["crupier"]["cartas_visibles"] == ["10D"]


def test_pasarse_termina_sin_que_juegue_el_crupier(partida_con, accion, contar):
    p = partida_con("10C", "10D", "9C", "6D", "KC", "5D")
    q = accion(p, "pedir").json()
    assert (q["estado"], q["resultado"], q["jugador"]["total"]) == ("terminada", "perdida", 29)
    assert q["crupier"]["cartas_visibles"] == ["10D", "6D"]     # se descubre, pero no pide
    assert contar("cartas WHERE duenio = 'crupier'") == 2


def test_llegar_a_21_hace_jugar_al_crupier(partida_con, accion):
    # Jugador 10+9 pide un 2 = 21. Crupier 10+6 = 16 pide un 3 = 19
    p = partida_con("10C", "10D", "9C", "6D", "2C", "3D")
    q = accion(p, "pedir").json()
    assert (q["estado"], q["resultado"]) == ("terminada", "ganada")
    assert q["crupier"] == {"cartas_visibles": ["10D", "6D", "3D"], "total_visible": 19}


# ---------------- Plantarse ----------------

def test_plantarse_hace_jugar_al_crupier(partida_con, accion, contar):
    # Jugador 18. Crupier 16 pide un 5 = 21
    p = partida_con("10C", "10D", "8C", "6D", "5D")
    q = accion(p, "plantarse").json()
    assert (q["estado"], q["resultado"], q["version"]) == ("terminada", "perdida", 2)
    assert q["crupier"]["total_visible"] == 21
    assert contar("cartas WHERE duenio = 'jugador'") == 2


def test_plantarse_empate(partida_con, accion):
    p = partida_con("10C", "10D", "8C", "8D")
    q = accion(p, "plantarse").json()
    assert q["resultado"] == "empate"


def test_terminada_guarda_hora_de_fin_y_cartas_segun_el_mazo(pool, partida_con, accion, contar):
    """R3 y R5 después de una partida completa con varios pedir."""
    p = partida_con("2C", "10D", "3C", "6D", "4C", "5C", "9D")
    p = accion(p, "pedir").json()
    p = accion(p, "pedir").json()
    accion(p, "plantarse")
    with pool.connection() as conexion:
        estado, fin, pos = conexion.execute(
            "SELECT estado, terminada_en IS NOT NULL, pos_mazo FROM partidas").fetchone()
    malas = contar("cartas c JOIN partidas p ON p.id = c.partida_id WHERE p.mazo[c.orden] <> c.carta")
    assert (estado, fin, malas) == ("terminada", True, 0)
    assert pos == contar("cartas")


# ---------------- Versión ----------------

def test_version_vieja_es_409_con_el_estado_actual(partida_con, accion, contar):
    p = partida_con("2C", "10D", "3C", "7D", "4C", "5C")
    accion(p, "pedir")                                   # la partida pasa a versión 2
    r = accion(p, "pedir", version=1)                    # otra decisión, tomada viendo la versión 1
    assert r.status_code == 409
    assert r.json()["version"] == 2 and len(r.json()["jugador"]["cartas"]) == 3
    assert contar("cartas WHERE duenio = 'jugador'") == 3   # no se aplicó nada


def test_actuar_en_partida_terminada_es_409(partida_con, accion):
    p = partida_con("10C", "10D", "8C", "8D")
    q = accion(p, "plantarse").json()
    r = accion(q, "pedir")
    assert r.status_code == 409 and r.json()["estado"] == "terminada"


# ---------------- Idempotencia ----------------

def test_reintento_con_version_vieja_devuelve_lo_guardado(partida_con, accion, contar):
    """La clave se revisa antes que la versión.

    El reintento llega con la versión 1 porque el cliente nunca recibió la 2.
    Si se revisara primero la versión, respondería 409 a una acción que sí ocurrió.
    """
    p = partida_con("2C", "10D", "3C", "7D", "4C", "5C")
    clave = uuid.uuid4()
    primera = accion(p, "pedir", version=1, clave=clave)
    reintento = accion(p, "pedir", version=1, clave=clave)
    assert (primera.status_code, reintento.status_code) == (200, 200)
    assert reintento.json() == primera.json()
    assert contar("cartas WHERE duenio = 'jugador'") == 3     # una sola carta nueva (R6)


def test_clave_de_otra_partida_es_422(partida_con, accion):
    a = partida_con("2C", "10D", "3C", "7D")
    clave = uuid.uuid4()
    accion(a, "pedir", clave=clave)
    b = partida_con("2D", "10C", "3D", "7C")
    r = accion(b, "pedir", clave=clave)
    assert r.status_code == 422 and r.json()["error"] == "la clave ya se usó en otra partida"


def test_clave_de_pedir_usada_en_plantarse_es_422(partida_con, accion, contar):
    """Misma partida, otra operación: no es un reintento y no se aplica nada."""
    p = partida_con("2C", "10D", "3C", "7D", "4C")
    clave = uuid.uuid4()
    q = accion(p, "pedir", clave=clave).json()
    r = accion(q, "plantarse", clave=clave)
    assert r.status_code == 422 and r.json()["error"] == "la clave ya se usó para 'pedir'"
    assert contar("partidas WHERE version = 2 AND estado = 'en_juego'") == 1
    assert contar("solicitudes") == 2                         # crear y el pedir


def test_reintentos_simultaneos_de_pedir(pool, partida_con, en_paralelo, contar):
    """5 copias del mismo pedir a la vez: el bloqueo las forma en fila, la
    primera aplica y las demás encuentran la clave."""
    p = partida_con("2C", "10D", "3C", "7D", "4C", "5C")
    clave = uuid.uuid4()
    resultados = en_paralelo(5, lambda _: partidas.pedir(pool, p["id"], clave, 1))
    assert [c for c, _ in resultados] == [200] * 5
    assert all(cuerpo == resultados[0][1] for _, cuerpo in resultados)
    assert contar("cartas WHERE duenio = 'jugador'") == 3


def test_dos_pedir_distintos_a_la_vez(pool, partida_con, en_paralelo, contar):
    """El caso de E3 (concurrencia): dos decisiones distintas sobre la misma mano.

    El bloqueo hace esperar a la segunda, y la versión la rechaza con 409
    porque se decidió viendo una mano que ya cambió. Nunca un error 500.
    """
    p = partida_con("2C", "10D", "3C", "7D", "4C", "5C")
    resultados = en_paralelo(2, lambda _: partidas.pedir(pool, p["id"], uuid.uuid4(), 1))
    assert sorted(c for c, _ in resultados) == [200, 409]
    assert contar("cartas WHERE duenio = 'jugador'") == 3


def test_misma_clave_en_dos_partidas_a_la_vez(partida_con, accion, en_paralelo, contar,
                                               monkeypatch):
    """El respaldo de r2_clave_unica en pedir y plantarse.

    Son dos filas distintas, así que el bloqueo no forma a las solicitudes en
    fila. Se detiene a las dos justo antes de guardar la clave, cuando ninguna
    la encontró: una la guarda y la otra choca con la llave primaria, se
    deshace y responde 422 porque la clave es de otra partida.
    """
    a = partida_con("2C", "10D", "3C", "7D", "4C")
    b = partida_con("2D", "10C", "3D", "7C", "4D")
    juntas = threading.Barrier(2, timeout=10)
    guardar, tras_choque, choques = partidas._guardar_solicitud, partidas._respuesta_tras_choque, []

    def guardar_juntas(*args):
        juntas.wait()
        guardar(*args)

    def contar_choque(*args):
        choques.append(args)
        return tras_choque(*args)

    monkeypatch.setattr(partidas, "_guardar_solicitud", guardar_juntas)
    monkeypatch.setattr(partidas, "_respuesta_tras_choque", contar_choque)
    clave = uuid.uuid4()
    por_codigo = {r.status_code: r.json()
                  for r in en_paralelo(2, lambda i: accion((a, b)[i], "pedir", clave=clave))}
    assert sorted(por_codigo) == [200, 422]
    assert por_codigo[422]["error"] == "la clave ya se usó en otra partida"
    assert len(choques) == 1                                 # pasó por el respaldo
    assert contar("cartas WHERE duenio = 'jugador'") == 5     # 2 + 2 y una sola carta nueva
    assert contar("solicitudes") == 3                         # dos crear y un pedir


# ---------------- Solicitudes inválidas ----------------

@pytest.mark.parametrize("operacion", ["pedir", "plantarse"])
def test_actuar_en_partida_que_no_existe_es_404(accion, operacion):
    r = accion({"id": uuid.uuid4(), "version": 1}, operacion)
    assert r.status_code == 404


@pytest.mark.parametrize("cuerpo", [
    {}, {"version": 0}, {"version": "uno"},
    {"version": "1"},                 # texto, aunque parezca número
    {"version": True},                # booleano
    {"version": 1.5},
    {"version": 1, "extra": 1},       # campo que no existe
])
def test_version_invalida_es_422(cliente, partida_con, cuerpo):
    p = partida_con("2C", "10D", "3C", "7D")
    r = cliente.post(f"/partidas/{p['id']}/pedir", json=cuerpo,
                     headers={"Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 422
