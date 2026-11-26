#!/usr/bin/env bash
# Uso: probar.sh [opciones de pytest]
# Corre todas las pruebas contra un PostgreSQL desechable en Docker (no toca el
# clúster). Crea el esquema con bd/esquema.sql y borra el contenedor al terminar.
# Requiere: pip install -r requirements-dev.txt en .venv
set -euo pipefail
source "$(dirname "$0")/comun.sh"

nombre=blackjack-pruebas-bd
puerto=55432

# Docker del equipo, no el de Minikube (por si quedó activo minikube docker-env)
unset DOCKER_HOST DOCKER_TLS_VERIFY DOCKER_CERT_PATH MINIKUBE_ACTIVE_DOCKERD

docker rm -f "$nombre" >/dev/null 2>&1 || true
docker run -d --rm --name "$nombre" -p 127.0.0.1:$puerto:5432 \
  -e POSTGRES_USER=blackjack -e POSTGRES_PASSWORD=pruebas -e POSTGRES_DB=blackjack \
  postgres:16.16 >/dev/null
trap 'docker rm -f "$nombre" >/dev/null 2>&1' EXIT

# Por TCP: el servidor temporal de initdb solo escucha en el socket local,
# así que esta consulta no pasa hasta que el servidor definitivo está listo
for _ in $(seq 60); do
  docker exec "$nombre" psql -h 127.0.0.1 -U blackjack -d blackjack -c "SELECT 1" >/dev/null 2>&1 && break
  sleep 1
done
docker exec -i "$nombre" psql -q -h 127.0.0.1 -U blackjack -d blackjack < "$RAIZ/bd/esquema.sql" >/dev/null
msg "PostgreSQL de pruebas listo en el puerto $puerto"

cd "$RAIZ"
# REQUIERE_BD=1: aquí la BD existe, así que una prueba que no la encuentre
# falla en lugar de saltarse en silencio. -rs lista cualquier otro salto
DB_HOST=127.0.0.1 DB_PORT=$puerto POSTGRES_DB=blackjack POSTGRES_USER=blackjack \
POSTGRES_PASSWORD=pruebas POOL_SIZE=10 REQUIERE_BD=1 \
  .venv/bin/python -m pytest -rs "$@"
