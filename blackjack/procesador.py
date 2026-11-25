import os
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from psycopg import OperationalError
from psycopg.errors import UndefinedTable
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolTimeout

from contrato import Accion, Clave, CrearPartida
from reglas import mezclar, resultado_codigos, total_codigos


@asynccontextmanager
async def lifespan(app):
    app.state.pool = ConnectionPool(
        os.environ["DATABASE_URL"],
        min_size=1,
        max_size=int(os.getenv("DB_POOL_SIZE", "10")),
        timeout=2,
        kwargs={"row_factory": dict_row, "connect_timeout": 2},
        open=False,
    )
    app.state.idempotencia = os.getenv("IDEMPOTENCIA", "on") != "off"
    app.state.pool.open()
    try:
        yield
    finally:
        app.state.pool.close()


app = FastAPI(lifespan=lifespan)


@app.exception_handler(PoolTimeout)
@app.exception_handler(OperationalError)
@app.exception_handler(UndefinedTable)
async def base_no_disponible(request, exc):
    return JSONResponse({"detail": "PostgreSQL no disponible"}, status_code=503)


@app.get("/salud")
def salud():
    return {"estado": "ok"}


@app.get("/listo")
def listo():
    with app.state.pool.connection() as conn:
        conn.execute("SELECT 1 FROM partidas LIMIT 1")
    return {"estado": "listo"}


def vista(partida, manos):
    jugador = manos["jugador"]
    crupier = manos["crupier"]
    if partida["estado"] == "en_juego":
        crupier = crupier[:1]
    return {
        "id": str(partida["id"]),
        "estado": partida["estado"],
        "version": partida["version"],
        "jugador": {"cartas": jugador, "total": total_codigos(jugador)},
        "crupier": {"cartas_visibles": crupier, "total_visible": total_codigos(crupier)},
        "resultado": partida["resultado"],
    }


def cargar_manos(conn, partida_id):
    manos = {"jugador": [], "crupier": []}
    for fila in conn.execute(
        "SELECT dueno, carta FROM cartas WHERE partida_id = %s ORDER BY orden",
        (partida_id,),
    ):
        manos[fila["dueno"]].append(fila["carta"])
    return manos


def repartir(conn, partida, manos, dueno):
    pos = partida["pos_mazo"]
    carta = partida["mazo"][pos]
    partida["pos_mazo"] = pos + 1
    manos[dueno].append(carta)
    conn.execute(
        "INSERT INTO cartas (partida_id, orden, dueno, carta) VALUES (%s, %s, %s, %s)",
        (partida["id"], pos + 1, dueno, carta),
    )


def finalizar(partida, manos):
    partida["estado"] = "terminada"
    partida["resultado"] = resultado_codigos(manos["jugador"], manos["crupier"])


class ClaveOcupada(Exception):
    """Provoca rollback si otra transacción registró antes la misma clave."""


def respuesta_guardada(conn, clave, operacion, partida_id=None):
    fila = conn.execute(
        "SELECT partida_id, operacion, respuesta FROM solicitudes WHERE clave = %s",
        (clave,),
    ).fetchone()
    if fila is None:
        return None
    if fila["operacion"] != operacion or (
        partida_id is not None and fila["partida_id"] != partida_id
    ):
        return JSONResponse({"detail": "Clave usada en otra operación"}, status_code=422)
    return JSONResponse(fila["respuesta"])


def registrar(conn, clave, partida_id, operacion, respuesta):
    insercion = conn.execute(
        """INSERT INTO solicitudes (clave, partida_id, operacion, respuesta)
           VALUES (%s, %s, %s, %s) ON CONFLICT (clave) DO NOTHING""",
        (clave, partida_id, operacion, Jsonb(respuesta)),
    )
    if insercion.rowcount == 0:
        raise ClaveOcupada


def guardar(conn, partida):
    conn.execute(
        """UPDATE partidas SET estado = %(estado)s, version = %(version)s,
           pos_mazo = %(pos_mazo)s, resultado = %(resultado)s,
           terminada_en = CASE WHEN %(estado)s = 'terminada' THEN now() END
           WHERE id = %(id)s""",
        partida,
    )


@app.post("/partidas", status_code=201)
def crear(body: CrearPartida, clave: Clave):
    if not app.state.idempotencia:
        clave = uuid4()
    try:
        with app.state.pool.connection() as conn:
            guardada = respuesta_guardada(conn, clave, "crear")
            if guardada is not None:
                return guardada
            partida = {
                "id": uuid4(), "estado": "en_juego", "version": 1,
                "mazo": mezclar(), "pos_mazo": 0, "resultado": None,
            }
            conn.execute(
                """INSERT INTO partidas (id, jugador, estado, version, mazo, pos_mazo)
                   VALUES (%s, %s, 'en_juego', 1, %s, 4)""",
                (partida["id"], body.jugador, partida["mazo"]),
            )
            manos = {"jugador": [], "crupier": []}
            for dueno in ("jugador", "crupier", "jugador", "crupier"):
                repartir(conn, partida, manos, dueno)
            if total_codigos(manos["jugador"]) == 21:
                finalizar(partida, manos)
                guardar(conn, partida)
            respuesta = vista(partida, manos)
            registrar(conn, clave, partida["id"], "crear", respuesta)
        return respuesta
    except ClaveOcupada:
        with app.state.pool.connection() as conn:
            return respuesta_guardada(conn, clave, "crear")


def inexistente():
    return JSONResponse({"detail": "Partida no encontrada"}, status_code=404)


@app.get("/partidas/{id}")
def consultar(id: UUID):
    with app.state.pool.connection() as conn:
        # Una instantánea para que la versión y las cartas correspondan al mismo estado.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        partida = conn.execute("SELECT * FROM partidas WHERE id = %s", (id,)).fetchone()
        if partida is None:
            return inexistente()
        return vista(partida, cargar_manos(conn, id))


def actuar(id, version, clave, operacion):
    if not app.state.idempotencia:
        clave = uuid4()
    try:
        with app.state.pool.connection() as conn:
            partida = conn.execute(
                "SELECT * FROM partidas WHERE id = %s FOR UPDATE", (id,)
            ).fetchone()
            if partida is None:
                return inexistente()
            guardada = respuesta_guardada(conn, clave, operacion, id)
            if guardada is not None:
                return guardada
            manos = cargar_manos(conn, id)
            if partida["version"] != version or partida["estado"] != "en_juego":
                return JSONResponse(vista(partida, manos), status_code=409)

            if operacion == "pedir":
                repartir(conn, partida, manos, "jugador")
            total = total_codigos(manos["jugador"])
            if total > 21:
                finalizar(partida, manos)
            elif operacion == "plantarse" or total == 21:
                while total_codigos(manos["crupier"]) < 17:
                    repartir(conn, partida, manos, "crupier")
                finalizar(partida, manos)
            partida["version"] += 1
            guardar(conn, partida)
            respuesta = vista(partida, manos)
            registrar(conn, clave, id, operacion, respuesta)
        return respuesta
    except ClaveOcupada:
        with app.state.pool.connection() as conn:
            return respuesta_guardada(conn, clave, operacion, id)


@app.post("/partidas/{id}/pedir")
def pedir(id: UUID, body: Accion, clave: Clave):
    return actuar(id, body.version, clave, "pedir")


@app.post("/partidas/{id}/plantarse")
def plantarse(id: UUID, body: Accion, clave: Clave):
    return actuar(id, body.version, clave, "plantarse")
