#!/usr/bin/env bash
# Uso: eliminar_pod.sh procesador|postgres ordenada|forzada|reinicio [ARCHIVO.csv]
# Provoca un fallo y mide cuánto tarda en recuperarse (E4 y E5).
# ordenada = delete, forzada = sin gracia, reinicio = rollout restart.
# Usa UID porque el pod nuevo de un StatefulSet conserva el nombre.
set -euo pipefail
source "$(dirname "$0")/comun.sh"
uso="Uso: eliminar_pod.sh procesador|postgres ordenada|forzada|reinicio [ARCHIVO.csv]"
LIMITE_RECUPERACION_S=${LIMITE_RECUPERACION_S:-300}   # tope de espera a la recuperación
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

# Todas las réplicas listas son pods nuevos (reinicio), o bien réplicas completas
# sin contar a la víctima (los demás modos)
listos_nuevos() {
  if [ "$modo" = reinicio ]; then
    [ "$(uids_listos | grep -cvxF -f <(echo "$uids_antes"))" -ge "$replicas" ]
  else
    [ "$(uids_listos | grep -cvxF "$uid_victima")" -ge "$replicas" ]
  fi
}
# Con tope de tiempo: si el clúster no se recupera, que el script termine con error
con_limite "$LIMITE_RECUPERACION_S" listos_nuevos \
  || { msg "ERROR: $objetivo no recuperó sus réplicas en $LIMITE_RECUPERACION_S s"; exit 1; }
segundos=$(segundos_desde "$t0")

# En PostgreSQL también se mide cuándo acepta consultas
seg_bd=""
if [ "$objetivo" = postgres ]; then esperar_bd; seg_bd=$(segundos_desde "$t0"); fi
msg "Réplicas listas otra vez tras $segundos s${seg_bd:+; la BD responde tras $seg_bd s}"

if [ -n "$archivo" ]; then
  csv_con_encabezado "$archivo" "marca_fallo,objetivo,modo,pods_afectados,replicas,segundos_hasta_listo,segundos_hasta_bd"
  echo "$marca,$objetivo,$modo,$afectados,$replicas,$segundos,$seg_bd" >> "$archivo"
fi
echo "$segundos"
