"""Paridad del contrato: la API y el procesador validan el cuerpo exactamente igual."""
import importlib.util
import pathlib

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[2]


def _cargar(nombre, ruta):
    espec = importlib.util.spec_from_file_location(nombre, ruta)
    m = importlib.util.module_from_spec(espec)
    espec.loader.exec_module(m)
    return m


@pytest.fixture
def modelos(monkeypatch):
    for k, v in {"PROCESADOR_URL": "http://procesador", "DB_HOST": "bd",
                 "POSTGRES_DB": "x", "POSTGRES_USER": "x", "POSTGRES_PASSWORD": "x"}.items():
        monkeypatch.setenv(k, v)
    # El pool del procesador se crea con open=False: no conecta
    return (_cargar("api_main_contrato", RAIZ / "api" / "main.py"),
            _cargar("procesador_main", RAIZ / "procesador" / "main.py"))


@pytest.mark.parametrize("nombre", ["NuevaPartida", "Accion"])
def test_mismo_esquema(modelos, nombre):
    api, proc = modelos
    assert getattr(api, nombre).model_json_schema() == getattr(proc, nombre).model_json_schema()
