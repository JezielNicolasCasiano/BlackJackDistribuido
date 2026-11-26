#!/usr/bin/env bash
# Uso: idempotencia.sh on|off
# Enciende o apaga la clave de idempotencia del procesador (experimento de control).
# Usa set env y no apply, para no deshacer un kubectl scale. kubectl apply -f k8s/
# la regresa a "on", el valor del ConfigMap.
set -euo pipefail
source "$(dirname "$0")/comun.sh"
modo=${1:?Uso: idempotencia.sh on|off}
[[ "$modo" =~ ^(on|off)$ ]] || { msg "Modo inválido: $modo (on u off)"; exit 1; }

k set env deployment/procesador IDEMPOTENCIA="$modo" >/dev/null
k rollout status deployment/procesador --timeout=180s >/dev/null
# Espera a que no queden pods con el modo anterior (preStop los deja vivos 5 s)
until [ "$(k get pods -l app=procesador --no-headers | wc -l)" -eq \
        "$(k get deploy procesador -o jsonpath='{.spec.replicas}')" ]; do sleep 1; done
for p in $(k get pods -l app=procesador -o name); do
  actual=$(k exec "$p" -- python -c "import urllib.request,json; print(json.load(urllib.request.urlopen('http://localhost:8000/quien'))['idempotencia'])")
  [ "$actual" = "$modo" ] || { msg "ERROR: $p sigue en $actual"; exit 1; }
done
esperar_cadena
msg "Idempotencia en $modo en todas las réplicas"
