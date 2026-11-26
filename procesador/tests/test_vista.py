"""Cómo ve el jugador una partida. Sin base de datos."""
from partidas import vista


def test_en_juego_oculta_la_segunda_carta_del_crupier():
    v = vista("p1", "en_juego", 1, ["7C", "9D"], ["AT", "KP"], None)
    assert v["crupier"] == {"cartas_visibles": ["AT"], "total_visible": 11}
    assert "KP" not in str(v)                     # la oculta no viaja en ningún campo
    assert v["jugador"] == {"cartas": ["7C", "9D"], "total": 16}


def test_terminada_muestra_toda_la_mano_del_crupier():
    v = vista("p1", "terminada", 3, ["7C", "9D", "5T"], ["AT", "KP"], "perdida")
    assert v["crupier"] == {"cartas_visibles": ["AT", "KP"], "total_visible": 21}
    assert v["resultado"] == "perdida"


def test_forma_de_la_respuesta():
    v = vista("p1", "en_juego", 1, ["7C", "9D"], ["AT", "KP"], None)
    assert set(v) == {"id", "estado", "version", "jugador", "crupier", "resultado"}
