#!/usr/bin/env bash
# Uso: jugar.sh PRUEBA [NOMBRE=valor ...]
# Una corrida completa de una prueba. Vacía la BD, juega con k6 y verifica, y
# guarda todo en resultados/PRUEBA/<fecha-hora>/ (diseño, sección 9.4):
#   parametros.txt      lo que se pidió y el modo de idempotencia
#   condiciones.txt     estado del equipo y del clúster antes de empezar
#   k6.csv.gz           cada métrica de cada solicitud (salida CSV de k6)
#   claves.jsonl.gz     cada acción: clave, códigos recibidos y si se confirmó
#   resumen.txt/.json   resumen de k6, con la fase de medición aparte
#   recursos.csv        kubectl top cada 15 s
#   pods.csv            cada cambio de estado de los pods
#   fallos.csv          el fallo provocado, si lo hubo
#   bd/, verificacion*.csv   copia de las tablas y verificación (verificar.sh)
#
# NOMBRE=valor se pasa a k6 (VUS, DURACION_S, CALENTAMIENTO_S, PAUSA_S,
# CONCURRENTES, INTENTOS, ESPERA_S, TIMEOUT: ver scripts/k6_jugadores.js), salvo:
#   FALLO="procesador forzada"   fallo que se provoca con eliminar_pod.sh...
#   FALLO_S=60                   ...a los 60 s de medición (tras el calentamiento)
# Ejemplo: jugar.sh e4 VUS=20 DURACION_S=180 FALLO="procesador forzada" FALLO_S=60
set -euo pipefail
source "$(dirname "$0")/comun.sh"
prueba=${1:?Uso: jugar.sh PRUEBA [NOMBRE=valor ...]}; shift

vars_k6=(); fallo=""; fallo_s=60; calentamiento=30
for par in "$@"; do
  case "$par" in
    FALLO=*)   fallo=${par#FALLO=} ;;
    FALLO_S=*) fallo_s=${par#FALLO_S=} ;;
    *=*)       vars_k6+=(-e "$par"); [[ $par == CALENTAMIENTO_S=* ]] && calentamiento=${par#*=} ;;
    *)         msg "Argumento inválido: $par (se espera NOMBRE=valor)"; exit 1 ;;
  esac
done

# Relativo a la raíz: k6 corre en un contenedor con el repo montado en /repo
rel="resultados/$prueba/$(date +%Y-%m-%d_%H-%M-%S)"
dir="$RAIZ/$rel"
mkdir -p "$dir"

"$RAIZ/scripts/reiniciar_datos.sh"
"$RAIZ/scripts/condiciones.sh" "$dir/condiciones.txt"
{
  echo "prueba: $prueba"
  echo "argumentos: $*"
  echo "idempotencia: $(k exec deploy/procesador -- printenv IDEMPOTENCIA 2>/dev/null || echo on)"
  echo "replicas_procesador: $(k get deploy procesador -o jsonpath='{.spec.replicas}')"
} > "$dir/parametros.txt"

# Mediciones en segundo plano mientras dura k6
pids=()
"$RAIZ/scripts/capturar_recursos.sh" "$dir/recursos.csv" 15 2>/dev/null & pids+=($!)
"$RAIZ/scripts/vigilar_pods.sh" "$dir/pods.csv" 2>/dev/null & pids+=($!)
detener() { kill "${pids[@]}" 2>/dev/null || true; wait "${pids[@]}" 2>/dev/null || true; }
trap detener EXIT

if [ -n "$fallo" ]; then
  # shellcheck disable=SC2086  # FALLO lleva dos palabras: objetivo y modo
  ( sleep $((calentamiento + fallo_s)); "$RAIZ/scripts/eliminar_pod.sh" $fallo "$dir/fallos.csv" >/dev/null ) &
  pids+=($!)
  msg "Fallo programado: $fallo a los $fallo_s s de medición"
fi

msg "Corriendo k6 ($prueba): ${*:-valores por defecto}"
codigo=0
"$RAIZ/scripts/k6.sh" run -q "${vars_k6[@]}" \
  -e RESUMEN="$rel/resumen.json" \
  --out csv="$rel/k6.csv.gz" \
  --log-format=raw --console-output="$rel/claves.jsonl" \
  scripts/k6_jugadores.js | tee "$dir/resumen.txt" || codigo=$?
[ "$codigo" -eq 0 ] || msg "AVISO: k6 terminó con código $codigo"

# Si el fallo sigue en curso (pod aún no listo) se espera antes de copiar la BD
[ -n "$fallo" ] && wait "${pids[-1]}" 2>/dev/null || true
detener; trap - EXIT

"$RAIZ/scripts/verificar.sh" "$dir" | tee -a "$dir/resumen.txt"
gzip "$dir/claves.jsonl"
msg "Resultados en $rel"
