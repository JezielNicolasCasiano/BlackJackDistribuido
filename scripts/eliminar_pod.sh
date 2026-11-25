#!/usr/bin/env bash
# Uso: eliminar_pod.sh procesador|postgres ordenada|forzada|reinicio [ARCHIVO.csv]
# Provoca un fallo y mide cuánto tarda en recuperarse (E4 y E5).
# ordenada = delete, forzada = sin gracia, reinicio = rollout restart.
# Usa UID porque el pod nuevo de un StatefulSet conserva el nombre.
set -euo pipefail
source "$(dirname "$0")/comun.sh"
uso="Uso: eliminar_pod.sh procesador|postgres ordenada|forzada|reinicio [ARCHIVO.csv]"
objetivo=${1:?$uso}; modo=${2:?$uso}; archivo=${3:-}

case "$objetivo" in
  procesador) controlador=deployment/procesador ;;
  postgres)   controlador=statefulset/postgres ;;
  *) msg "Objetivo inválido: $objetivo"; msg "$uso"; exit 1 ;;
esac
replicas=$(k get "$controlador" -o jsonpath='{.spec.replicas}')
uids_antes=$(k get pods -l app="$objetivo" -o jsonpath='{range .items[*]}{.metadata.uid}{"\n"}{end}')
victima=$(k get pods -l app="$objetivo" -o jsonpath='{.items[0].metadata.name}')

uid_victima=$(k get pod "$victima" -o jsonpath='{.metadata.uid}')

uids_listos() {
  k get pods -l app="$objetivo" -o jsonpath='{range .items[*]}{.metadata.uid} {.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' \
    | awk '$2 == "True" {print $1}'
}

t0=$(date +%s.%N); marca=$(ahora)
case "$modo" in
  ordenada) k delete pod "$victima" --wait=false >/dev/null 2>&1; afectados=$victima ;;
  forzada)  k delete pod "$victima" --wait=false --grace-period=0 --force >/dev/null 2>&1; afectados=$victima ;;
  reinicio) k rollout restart "$controlador" >/dev/null; afectados="todos ($replicas)" ;;
  *) msg "Modo inválido: $modo"; msg "$uso"; exit 1 ;;
esac
msg "Fallo provocado en $objetivo ($modo, $afectados) a las $marca"

if [ "$modo" = reinicio ]; then
  # Todas las réplicas listas son pods nuevos
  until [ "$(uids_listos | grep -cvxF -f <(echo "$uids_antes"))" -ge "$replicas" ]; do sleep 0.25; done
else
  # Réplicas completas sin contar a la víctima
  until [ "$(uids_listos | grep -cvxF "$uid_victima")" -ge "$replicas" ]; do sleep 0.25; done
fi
segundos=$(python3 -c "print(f'{$(date +%s.%N) - $t0:.2f}')")

# En PostgreSQL también se mide cuándo acepta consultas
seg_bd=""
if [ "$objetivo" = postgres ]; then esperar_bd; seg_bd=$(python3 -c "print(f'{$(date +%s.%N) - $t0:.2f}')"); fi
msg "Réplicas listas otra vez tras $segundos s${seg_bd:+; la BD responde tras $seg_bd s}"

if [ -n "$archivo" ]; then
  mkdir -p "$(dirname "$archivo")"
  [ -s "$archivo" ] || echo "marca_fallo,objetivo,modo,pods_afectados,replicas,segundos_hasta_listo,segundos_hasta_bd" > "$archivo"
  echo "$marca,$objetivo,$modo,$afectados,$replicas,$segundos,$seg_bd" >> "$archivo"
fi
echo "$segundos"
