#!/usr/bin/env bash
# Uso: vigilar_pods.sh ARCHIVO.csv
# Registra cada cambio de estado de los pods con su hora. Corre hasta kill.
set -uo pipefail
source "$(dirname "$0")/comun.sh"
archivo=${1:?Uso: vigilar_pods.sh ARCHIVO.csv}
mkdir -p "$(dirname "$archivo")"
[ -s "$archivo" ] || echo "marca_tiempo,pod,fase,listo,en_eliminacion" > "$archivo"

# kubectl directo (sin tubería ni función k) para que $! sea su PID y no queden huérfanos
kubectl -n "$NS" get pods -w -o jsonpath='{.metadata.name},{.status.phase},{.status.conditions[?(@.type=="Ready")].status},{.metadata.deletionTimestamp}{"\n"}' \
  > >(while IFS=, read -r pod fase listo borrado; do
        echo "$(ahora),$pod,$fase,${listo:-False},$([ -n "$borrado" ] && echo sí || echo no)"
      done >> "$archivo") &
kpid=$!
trap 'trap - INT TERM; kill "$kpid" 2>/dev/null; wait "$kpid" 2>/dev/null; msg "Vigilancia detenida ($archivo)"; exit 0' INT TERM
wait "$kpid"
