#!/usr/bin/env bash
# Uso: k6.sh run ARCHIVO.js [opciones de k6]
# k6 en Docker dentro de la red de Minikube (bridge no llega, host falla fuera de Linux).
# Recibe API_URL y el repo montado en /repo. El contenedor tiene nombre fijo
# (K6_CONTENEDOR) para poder detenerlo por nombre sin depender de la versión de la imagen.
set -euo pipefail
source "$(dirname "$0")/comun.sh"

[ "$#" -ge 1 ] || { sed -n '2,4p' "$0"; exit 1; }
# Si ya hay un k6 corriendo (p. ej. el de jugar.sh) no se toca: borrarlo cortaría esa medición
if [ "$(docker inspect -f '{{.State.Running}}' "$K6_CONTENEDOR" 2>/dev/null)" = true ]; then
  msg "ERROR: ya hay un k6 corriendo ($K6_CONTENEDOR). Espera a que termine o detenlo con: docker stop $K6_CONTENEDOR"
  exit 1
fi
# Un contenedor detenido con el mismo nombre (de una corrida interrumpida) impediría arrancar
docker rm -f "$K6_CONTENEDOR" >/dev/null 2>&1 || true
docker run --rm -i \
  --name "$K6_CONTENEDOR" \
  --network minikube \
  --user "$(id -u):$(id -g)" \
  -v "$RAIZ":/repo -w /repo \
  -e API_URL="$(api_url)" \
  "$K6_IMAGEN" "$@"
