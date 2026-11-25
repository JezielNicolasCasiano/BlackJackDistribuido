#!/usr/bin/env bash
# Uso: k6.sh run ARCHIVO.js [opciones de k6]
# k6 en Docker dentro de la red de Minikube (bridge no llega, host falla fuera de Linux).
# Recibe API_URL y el repo montado en /repo.
set -euo pipefail
source "$(dirname "$0")/comun.sh"
K6_IMAGEN=grafana/k6:2.4.0

[ "$#" -ge 1 ] || { sed -n '2,4p' "$0"; exit 1; }
docker run --rm -i \
  --network minikube \
  --user "$(id -u):$(id -g)" \
  -v "$RAIZ":/repo -w /repo \
  -e API_URL="$(api_url)" \
  "$K6_IMAGEN" "$@"
