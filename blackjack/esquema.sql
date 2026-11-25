CREATE TABLE partidas (
    id uuid PRIMARY KEY,
    jugador text NOT NULL CHECK (btrim(jugador) <> ''),
    estado text NOT NULL CHECK (estado IN ('en_juego', 'terminada')),
    version integer NOT NULL CHECK (version >= 1),
    mazo text[] NOT NULL CHECK (array_ndims(mazo) = 1 AND cardinality(mazo) = 52),
    pos_mazo integer NOT NULL CHECK (pos_mazo BETWEEN 4 AND 52),
    resultado text CHECK (resultado IN ('ganada', 'perdida', 'empate')),
    creada_en timestamptz NOT NULL DEFAULT now(),
    terminada_en timestamptz,
    CHECK ((estado = 'terminada') = (resultado IS NOT NULL)),
    CHECK ((estado = 'terminada') = (terminada_en IS NOT NULL))
);

CREATE TABLE cartas (
    partida_id uuid REFERENCES partidas(id),
    orden integer CHECK (orden BETWEEN 1 AND 52),
    dueno text NOT NULL CHECK (dueno IN ('jugador', 'crupier')),
    carta text NOT NULL CHECK (carta ~ '^(A|[2-9]|10|J|Q|K)[CDTP]$'),
    PRIMARY KEY (partida_id, orden),
    UNIQUE (partida_id, carta)
);

CREATE TABLE solicitudes (
    clave uuid PRIMARY KEY,
    partida_id uuid NOT NULL REFERENCES partidas(id),
    operacion text NOT NULL CHECK (operacion IN ('crear', 'pedir', 'plantarse')),
    respuesta jsonb NOT NULL,
    creada_en timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX solicitudes_partida_idx ON solicitudes (partida_id);
