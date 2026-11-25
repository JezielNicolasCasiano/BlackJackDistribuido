"""Prueba funcional de la API local. Conserva las partidas de prueba en PostgreSQL."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


BASE = os.getenv("BLACKJACK_URL", "http://127.0.0.1:8000").rstrip("/")


def solicitar(ruta, datos=None, clave=None):
    headers = {"Content-Type": "application/json"}
    if clave is not None:
        headers["Idempotency-Key"] = str(clave)
    body = None if datos is None else json.dumps(datos).encode()
    request = Request(BASE + ruta, data=body, headers=headers)
    try:
        respuesta = urlopen(request, timeout=10)
    except HTTPError as error:
        respuesta = error
    with respuesta:
        return respuesta.status, json.load(respuesta)


def crear_con_reintento():
    clave = uuid4()
    datos = {"jugador": "prueba-funcional"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        primera = pool.submit(solicitar, "/partidas", datos, clave)
        segunda = pool.submit(solicitar, "/partidas", datos, clave)
        (codigo_a, estado_a), (codigo_b, estado_b) = primera.result(), segunda.result()
    assert sorted((codigo_a, codigo_b)) == [200, 201], (codigo_a, codigo_b)
    assert estado_a == estado_b
    assert estado_a["version"] == 1
    return estado_a


def probar():
    assert solicitar("/listo")[0] == 200
    assert solicitar(f"/partidas/{uuid4()}")[0] == 404
    assert solicitar("/partidas", {"jugador": ""}, uuid4())[0] == 422
    assert solicitar("/partidas", {"jugador": "J1"})[0] == 422
    for _ in range(20):
        inicial = crear_con_reintento()
        if inicial["estado"] == "en_juego":
            break
    else:
        raise AssertionError("No se obtuvo una partida en juego")

    ruta = f"/partidas/{inicial['id']}"
    assert solicitar(ruta) == (200, inicial)
    assert len(inicial["crupier"]["cartas_visibles"]) == 1
    assert solicitar(ruta + "/pedir", {"version": "1"}, uuid4())[0] == 422
    assert solicitar(ruta + "/pedir", {"version": 2}, uuid4()) == (409, inicial)

    with ThreadPoolExecutor(max_workers=2) as pool:
        clave_a, clave_b = uuid4(), uuid4()
        a = pool.submit(solicitar, ruta + "/pedir", {"version": 1}, clave_a)
        b = pool.submit(solicitar, ruta + "/pedir", {"version": 1}, clave_b)
        pares = sorted(((a.result(), clave_a), (b.result(), clave_b)), key=lambda p: p[0][0])
    ((codigo, nuevo), clave), ((conflicto, actual), _) = pares
    assert (codigo, conflicto) == (200, 409)
    assert nuevo == actual and nuevo["version"] == 2
    assert len(nuevo["jugador"]["cartas"]) == 3
    if nuevo["estado"] == "en_juego":
        codigo, final = solicitar(ruta + "/plantarse", {"version": 2}, uuid4())
        assert codigo == 200
    else:
        final = nuevo
    assert final["estado"] == "terminada" and final["resultado"] is not None
    assert len(final["crupier"]["cartas_visibles"]) >= 2
    assert solicitar(ruta) == (200, final)
    assert solicitar(ruta + "/pedir", {"version": 1}, clave) == (200, nuevo)
    assert solicitar(ruta + "/pedir", {"version": final["version"]}, uuid4()) == (409, final)
    print(f"Prueba funcional correcta. Partida guardada: {inicial['id']}")


if __name__ == "__main__":
    probar()
