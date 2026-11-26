"""Lo que comparten las pruebas de base de datos."""
import pytest


@pytest.fixture(scope="session")
def cliente():
    """Cliente HTTP del procesador, uno para toda la corrida.

    Abre el pool de conexiones (lifespan) una sola vez: psycopg no permite
    volver a abrir un pool cerrado, así que no puede abrirse uno por archivo.
    Se importa aquí dentro porque main necesita las variables de la BD, que
    solo existen al correr con scripts/probar.sh.
    """
    from fastapi.testclient import TestClient
    import main
    with TestClient(main.app) as c:
        yield c
