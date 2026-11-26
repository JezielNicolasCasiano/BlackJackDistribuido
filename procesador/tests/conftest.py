"""Lo que comparten las pruebas del procesador: la base de datos y ayudantes.

Las pruebas que usan la BD piden, directa o indirectamente, el fixture
procesador. Sin las variables de la BD se saltan; scripts/probar.sh pone
REQUIERE_BD=1 y entonces fallan, para que no pasen en verde sin haber corrido.
"""
import os
import threading
import uuid

import pytest

import partidas
from reglas import PALOS, VALORES

TODAS = [v + p for p in PALOS for v in VALORES]


def _sin_bd(motivo):
    if os.environ.get("REQUIERE_BD") == "1":
        pytest.fail(f"REQUIERE_BD=1 y {motivo}", pytrace=False)
    pytest.skip(f"sin base de datos ({motivo}): correr con scripts/probar.sh")


@pytest.fixture(scope="session")
def procesador():
    """El módulo main del procesador.

    Se importa aquí dentro porque main necesita las variables de la BD, que
    solo existen al correr con scripts/probar.sh.
    """
    if "DB_HOST" not in os.environ:
        _sin_bd("falta DB_HOST")
    import main
    return main


@pytest.fixture(scope="session")
def cliente(procesador):
    """Cliente HTTP del procesador, uno para toda la corrida.

    Abre el pool de conexiones (lifespan) una sola vez: psycopg no permite
    volver a abrir un pool cerrado, así que no puede abrirse uno por archivo.
    """
    from fastapi.testclient import TestClient
    with TestClient(procesador.app) as c:
        try:
            with procesador.pool.connection(timeout=10) as conexion:
                conexion.execute("SELECT 1")
        except Exception as error:
            _sin_bd(f"la BD no responde ({type(error).__name__})")
        yield c


@pytest.fixture
def pool(cliente, procesador):
    """El pool del procesador, ya abierto."""
    return procesador.pool


@pytest.fixture
def bd_vacia(pool):
    """Tablas vacías al empezar cada prueba. Un archivo lo activa para todas
    sus pruebas con pytestmark = pytest.mark.usefixtures("bd_vacia")."""
    with pool.connection() as conexion:
        conexion.execute("TRUNCATE partidas, cartas, solicitudes")


@pytest.fixture
def contar(pool):
    """count(*) de lo que va después del FROM: contar("cartas WHERE duenio = 'crupier'")."""
    def contar(desde, *parametros):
        with pool.connection() as conexion:
            return conexion.execute(f"SELECT count(*) FROM {desde}",
                                    parametros or None).fetchone()[0]
    return contar


@pytest.fixture
def crear(cliente):
    """POST /partidas, con una clave nueva si no se da una."""
    def crear(clave=None, jugador="J1"):
        return cliente.post("/partidas", json={"jugador": jugador},
                            headers={"Idempotency-Key": str(clave or uuid.uuid4())})
    return crear


@pytest.fixture
def accion(cliente):
    """pedir o plantarse sobre la partida p (su vista), por omisión con la versión que trae."""
    def accion(p, operacion, version=None, clave=None):
        return cliente.post(f"/partidas/{p['id']}/{operacion}",
                            json={"version": version or p["version"]},
                            headers={"Idempotency-Key": str(clave or uuid.uuid4())})
    return accion


def mazo_con(*primeras):
    """Un mazo de 52 que empieza con las cartas dadas; el resto va en orden fijo."""
    return list(primeras) + [c for c in TODAS if c not in primeras]


@pytest.fixture
def partida_con(crear, monkeypatch):
    """Crea una partida con las primeras cartas del mazo elegidas a mano.

    El orden es el del reparto: jugador, crupier, jugador, crupier, y después
    las cartas que saldrán al pedir. Devuelve la vista de la partida.
    """
    def partida_con(*primeras):
        monkeypatch.setattr(partidas, "crear_mazo", lambda rng: mazo_con(*primeras))
        r = crear()
        assert r.status_code == 201
        return r.json()
    return partida_con


@pytest.fixture
def en_paralelo():
    """Corre fn(i) en n hilos que arrancan juntos (i va de 0 a n-1).

    Devuelve lo que devolvió cada hilo, en orden de i. Si alguno lanzó una
    excepción, se vuelve a lanzar aquí para que la prueba falle con ella.
    """
    def en_paralelo(n, fn):
        salida = threading.Barrier(n, timeout=10)
        resultados = [None] * n

        def correr(i):
            salida.wait()
            try:
                resultados[i] = fn(i)
            except BaseException as error:
                resultados[i] = error

        hilos = [threading.Thread(target=correr, args=(i,)) for i in range(n)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        for r in resultados:
            if isinstance(r, BaseException):
                raise r
        return resultados
    return en_paralelo
