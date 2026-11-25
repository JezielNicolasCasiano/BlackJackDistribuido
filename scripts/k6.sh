#!/usr/bin/env bash
# Uso: k6.sh run ARCHIVO.js [opciones de k6]
# Corre k6 (grafana/k6, versión fija) en Docker, unido a la red de Minikube.
#   - Con la red por defecto (bridge) k6 no alcanza el clúster: Docker aísla sus redes.
#   - --network host funciona en Linux, pero no en Windows ni macOS.
#   - --network minikube funciona en los tres (prueba de despliegue, sección 15).
# El repositorio se monta en /repo, así que las rutas relativas funcionan igual:
#   scripts/k6.sh run pruebas/e1.js --out csv=resultados/e1/salida.csv
# La variable API_URL llega al script de k6 con la dirección de la API.
set -euo pipefail
source "$(dirname "$0")/comun.sh"
K6_IMAGEN=grafana/k6:2.4.0

[ "$#" -ge 1 ] || { sed -n '2,10p' "$0"; exit 1; }
docker run --rm -i \
  --network minikube \
  --user "$(id -u):$(id -g)" \
  -v "$RAIZ":/repo -w /repo \
  -e API_URL="$(api_url)" \
  "$K6_IMAGEN" "$@"
