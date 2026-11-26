#!/usr/bin/env bash
# Uso: cpu_pods.sh ETIQUETA ARCHIVO.csv
# Agrega al CSV los contadores de CPU del cgroup de cada pod (cpu.stat). Con dos
# lecturas se obtiene el uso exacto entre ellas y cuántas veces el límite de CPU
# frenó al pod (throttling), cosa que kubectl top no muestra.
# Sin -e a propósito: si un pod no responde a la lectura se omite, no se aborta la corrida
set -uo pipefail
source "$(dirname "$0")/comun.sh"
etiqueta=${1:?Uso: cpu_pods.sh ETIQUETA ARCHIVO.csv}; archivo=${2:?Uso: cpu_pods.sh ETIQUETA ARCHIVO.csv}
csv_con_encabezado "$archivo" "etiqueta,marca_s,pod,usage_usec,nr_periods,nr_throttled,throttled_usec"

leer() {
  local pod=$1 t stat
  t=$(date +%s.%N)
  stat=$(k exec "$pod" -- cat /sys/fs/cgroup/cpu.stat 2>/dev/null) || return 0
  awk -v e="$etiqueta" -v t="$t" -v p="$pod" '
    {v[$1]=$2} END {printf "%s,%s,%s,%s,%s,%s,%s\n", e, t, p, v["usage_usec"], v["nr_periods"], v["nr_throttled"], v["throttled_usec"]}' <<< "$stat"
}
# En paralelo, para que todas las lecturas caigan casi en el mismo instante
for pod in $(k get pods -l 'app in (api,procesador,postgres)' --field-selector=status.phase=Running -o name); do
  leer "${pod#pod/}" &
done | sort >> "$archivo"
wait
