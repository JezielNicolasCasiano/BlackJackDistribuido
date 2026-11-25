#!/usr/bin/env bash
# Uso: capturar_recursos.sh ARCHIVO.csv [INTERVALO_S=15] [DURACION_S=0]
# Registra CPU y memoria por pod con kubectl top. Con DURACION_S=0 corre hasta
# que se detiene (Ctrl+C o kill). Pensado para correr en segundo plano:
#   scripts/capturar_recursos.sh resultados/e2/.../recursos.csv & PID=$!; ...; kill $PID
# El intervalo por defecto coincide con la resolución de metrics-server (15 s).
set -uo pipefail
source "$(dirname "$0")/comun.sh"
archivo=${1:?Uso: capturar_recursos.sh ARCHIVO.csv [INTERVALO_S] [DURACION_S]}
intervalo=${2:-15}; duracion=${3:-0}

mkdir -p "$(dirname "$archivo")"
[ -s "$archivo" ] || echo "marca_tiempo,pod,cpu_milicores,memoria_mib" > "$archivo"
trap 'msg "Captura detenida ($archivo)"; exit 0' INT TERM

inicio=$(date +%s)
while :; do
  t=$(ahora)
  k top pods --no-headers 2>/dev/null | awk -v t="$t" '{
      cpu=$2; if (cpu ~ /m$/) sub(/m$/,"",cpu); else cpu=cpu*1000;
      mem=$3; f=1; if (mem ~ /Ki$/) f=1/1024; else if (mem ~ /Gi$/) f=1024;
      sub(/[KMG]i$/,"",mem); printf "%s,%s,%s,%.1f\n", t, $1, cpu, mem*f }' >> "$archivo"
  [ "$duracion" -gt 0 ] && [ $(( $(date +%s) - inicio )) -ge "$duracion" ] && break
  # sleep en segundo plano + wait: así el trap responde al instante. Con un
  # sleep en primer plano, bash no atiende la señal hasta que el sleep termina.
  sleep "$intervalo" & wait $!
done
msg "Captura terminada ($archivo)"
