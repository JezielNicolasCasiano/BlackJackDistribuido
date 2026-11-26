#!/usr/bin/env bash
# Uso: escalar.sh N
# Escala el procesador y espera a que todas las réplicas reciban tráfico (E2)
set -euo pipefail
source "$(dirname "$0")/comun.sh"
n=${1:?Uso: escalar.sh N}

k scale deployment/procesador --replicas="$n" >/dev/null
esperar_rollout procesador   # incluye esperar a que no queden pods terminando
# El tráfico tarda ~1.2 s en llegar a un pod nuevo
sleep 5
msg "Procesador con $n réplica(s) lista(s)"
k get pods -l app=procesador --no-headers | awk '{print "  " $1, $2}' >&2
