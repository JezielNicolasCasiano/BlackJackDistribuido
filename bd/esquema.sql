-- Esquema del Blackjack distribuido: tres tablas y las reglas R1 a R4.
-- Se puede correr varias veces sin daño (IF NOT EXISTS). Para cambiar una
-- tabla que ya existe: scripts/crear_esquema.sh --desde-cero.
\set ON_ERROR_STOP on

-- Todo o nada: si una sentencia falla, no queda un esquema a medias
BEGIN;

-- Una fila por partida. Es la fila que bloquea SELECT ... FOR UPDATE,
-- por eso la baraja vive aquí y no en otra tabla.
CREATE TABLE IF NOT EXISTS partidas (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    jugador      text        NOT NULL CHECK (jugador <> ''),
    estado       text        NOT NULL DEFAULT 'en_juego',
    version      integer     NOT NULL DEFAULT 1 CHECK (version >= 1),
    -- Las 52 cartas ya mezcladas. Los arreglos de PostgreSQL empiezan en 1:
    -- la siguiente carta es mazo[pos_mazo + 1]
    mazo         text[]      NOT NULL CHECK (cardinality(mazo) = 52),
    -- Cuántas cartas se han repartido
    pos_mazo     integer     NOT NULL DEFAULT 0 CHECK (pos_mazo BETWEEN 0 AND 52),
    resultado    text,
    creada_en    timestamptz NOT NULL DEFAULT now(),
    terminada_en timestamptz,

    CONSTRAINT r4_estado_valido
        CHECK (estado IN ('en_juego', 'terminada')),
    CONSTRAINT r4_resultado_valido
        CHECK (resultado IN ('ganada', 'perdida', 'empate')),
    -- Terminada si y solo si tiene resultado. terminada_en no se vigila a
    -- propósito: lo debe escribir el procesador al terminar la partida
    CONSTRAINT r3_fin_con_resultado
        CHECK ((estado = 'terminada') = (resultado IS NOT NULL))
);

-- Una fila por carta repartida. orden es la posición de la carta en el
-- mazo, así que siempre se cumple mazo[orden] = carta (R5, se verifica aparte)
CREATE TABLE IF NOT EXISTS cartas (
    partida_id uuid     NOT NULL REFERENCES partidas (id),
    orden      smallint NOT NULL CHECK (orden BETWEEN 1 AND 52),
    duenio     text     NOT NULL,
    carta      text     NOT NULL,

    PRIMARY KEY (partida_id, orden),
    CONSTRAINT r1_carta_unica
        UNIQUE (partida_id, carta),
    CONSTRAINT r4_duenio_valido
        CHECK (duenio IN ('jugador', 'crupier')),
    -- Valor y palo: 7C, 10P, KT (C corazones, D diamantes, T tréboles, P picas)
    CONSTRAINT r4_carta_valida
        CHECK (carta ~ '^(A|[2-9]|10|J|Q|K)[CDTP]$')
);

-- Una fila por acción aplicada, con la respuesta que se devolvió.
-- Un reintento con la misma clave recibe esa misma respuesta.
CREATE TABLE IF NOT EXISTS solicitudes (
    clave      uuid        NOT NULL,
    partida_id uuid        NOT NULL REFERENCES partidas (id),
    operacion  text        NOT NULL,
    respuesta  jsonb       NOT NULL,
    creada_en  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT r2_clave_unica
        PRIMARY KEY (clave),
    CONSTRAINT r4_operacion_valida
        CHECK (operacion IN ('crear', 'pedir', 'plantarse'))
);

-- PostgreSQL no indexa solas las llaves foráneas. Sin esto, contar las
-- acciones de una partida (R6) recorrería la tabla completa
CREATE INDEX IF NOT EXISTS solicitudes_por_partida ON solicitudes (partida_id);

COMMIT;
