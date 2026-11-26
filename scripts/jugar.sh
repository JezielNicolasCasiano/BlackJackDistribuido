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
#   cpu.csv             CPU exacta por pod durante la medición y cuánto la frenó el límite
#   pods.csv            cada cambio de estado de los pods
#   fallos.csv          el fallo provocado, si lo hubo
#   temperatura.csv     temperatura de la CPU cada 2 s durante k6
#   api.log.gz, procesador.log.gz   registros de los pods (una línea por solicitud en la API)
#   bd/, verificacion*.csv   copia de las tablas y verificación (verificar.sh)
#
# NOMBRE=valor se pasa a k6 (VUS, DURACION_S, CALENTAMIENTO_S, PAUSA_S,
# CONCURRENTES, INTENTOS, ESPERA_S, TIMEOUT: ver scripts/k6_jugadores.js), salvo:
#   FALLO="procesador forzada"   fallo que se provoca con eliminar_pod.sh...
#   FALLO_S=60                   ...a los 60 s de medición (tras el calentamiento)
# Protección térmica: espera a que la CPU baje de TEMP_INICIO (60 °C) antes de
# empezar y detiene k6 si llega a TEMP_MAXIMA (90 °C); la corrida queda marcada
# como abortada en parametros.txt
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
# Justo antes de medir: vaciar la BD o escalar también calientan el equipo
esperar_frio
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

# Contadores de CPU justo al empezar y al terminar la medición
duracion=120
for par in "$@"; do [[ $par == DURACION_S=* ]] && duracion=${par#*=}; done
( sleep "$calentamiento"; "$RAIZ/scripts/cpu_pods.sh" inicio "$dir/cpu_contadores.csv"
  sleep "$duracion";      "$RAIZ/scripts/cpu_pods.sh" fin "$dir/cpu_contadores.csv" ) &
cpu_pid=$!

if [ -n "$fallo" ]; then
  # shellcheck disable=SC2086  # FALLO lleva dos palabras: objetivo y modo
  ( sleep $((calentamiento + fallo_s)); "$RAIZ/scripts/eliminar_pod.sh" $fallo "$dir/fallos.csv" >/dev/null ) &
  pids+=($!)
  msg "Fallo programado: $fallo a los $fallo_s s de medición"
fi

# Vigía térmico: detiene el contenedor de k6 si la CPU llega a TEMP_MAXIMA
( limite=${TEMP_MAXIMA:-90}
  echo "marca_tiempo,temperatura_c" > "$dir/temperatura.csv"
  while sleep 2; do
    t=$(temperatura); [ -n "$t" ] || exit 0
    echo "$(ahora),$t" >> "$dir/temperatura.csv"
    if [ "$t" -ge "$limite" ]; then
      msg "ABORTADA: CPU a ${t} °C (límite ${limite} °C); se detiene k6"
      echo "abortada: CPU a ${t} °C a las $(ahora)" >> "$dir/parametros.txt"
      docker ps -q --filter ancestor=grafana/k6:2.4.0 | xargs -r docker stop -t 1 >/dev/null
      exit 0
    fi
  done ) &
pids+=($!)

inicio_registros=$(date -u +%Y-%m-%dT%H:%M:%SZ)
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
# Tras un aborto no tiene caso esperar la lectura final de CPU
if grep -q '^abortada' "$dir/parametros.txt"; then kill "$cpu_pid" 2>/dev/null || true; fi
wait "$cpu_pid" 2>/dev/null || true
detener; trap - EXIT
python3 "$RAIZ/scripts/resumir_cpu.py" "$dir" 2>/dev/null | tee -a "$dir/resumen.txt" || true
[ -s "$dir/temperatura.csv" ] && awk -F, 'NR>1 {if ($2>m) m=$2} END {print "Temperatura máxima de la CPU: " m " °C"}' "$dir/temperatura.csv" | tee -a "$dir/resumen.txt"

# Registros de los pods que siguen vivos (los de un pod eliminado se pierden con él)
k logs -l app=api --prefix --tail=-1 --since-time="$inicio_registros" 2>/dev/null | gzip > "$dir/api.log.gz"
k logs -l app=procesador --prefix --tail=-1 --max-log-requests=10 --since-time="$inicio_registros" 2>/dev/null \
  | gzip > "$dir/procesador.log.gz"

"$RAIZ/scripts/verificar.sh" "$dir" | tee -a "$dir/resumen.txt"
gzip "$dir/claves.jsonl"
msg "Resultados en $rel"
