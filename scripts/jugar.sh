#!/usr/bin/env bash
# Uso: jugar.sh PRUEBA [NOMBRE=valor ...]
# Una corrida completa de una prueba. Vacía la BD, juega con k6 y verifica, y
# guarda todo en resultados/PRUEBA/<fecha-hora>/:
#   parametros.txt      lo pedido y lo resuelto: argumentos, valores finales de cada variable,
#                       idempotencia, réplicas, recursos e imágenes de api y procesador,
#                       commit del código, API_KEEPALIVE e imagen de k6. Si la corrida no
#                       termina normal se agrega "abortada:" (calor) o "interrumpida:" (Ctrl+C)
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
# Variables que se aceptan (cualquier otro nombre es un error, para cazar erratas
# como VU=10). Las de k6 se explican en scripts/k6_jugadores.js; entre paréntesis,
# el valor por omisión:
#   VUS (10)  DURACION_S (120)  CALENTAMIENTO_S (30)  PAUSA_S (0)
#   CONCURRENTES (0)  INTENTOS (3)  ESPERA_S (0.5)  TIMEOUT (8s)
#   FALLO="procesador forzada"   fallo que se provoca con eliminar_pod.sh...
#   FALLO_S=60                   ...a los 60 s de medición (tras el calentamiento)
#   TEMP_INICIO (60)  TEMP_MAXIMA (90)   protección térmica: espera a que la CPU baje de
#                       TEMP_INICIO °C antes de empezar y detiene k6 si llega a TEMP_MAXIMA °C
# Los tiempos de CPU y del fallo se cuentan desde que el contenedor de k6 está corriendo.
# Ejemplo: jugar.sh e4 VUS=20 DURACION_S=180 FALLO="procesador forzada" FALLO_S=60
set -euo pipefail
source "$(dirname "$0")/comun.sh"
prueba=${1:?Uso: jugar.sh PRUEBA [NOMBRE=valor ...]}; shift

# ---- Argumentos: se analizan una sola vez y se resuelven los valores por omisión ----
orden_k6=(VUS DURACION_S CALENTAMIENTO_S PAUSA_S CONCURRENTES INTENTOS ESPERA_S TIMEOUT)
declare -A v=([VUS]=10 [DURACION_S]=120 [CALENTAMIENTO_S]=30 [PAUSA_S]=0
              [CONCURRENTES]=0 [INTENTOS]=3 [ESPERA_S]=0.5 [TIMEOUT]=8s)
fallo=""; fallo_s=60

error_arg() { msg "Argumento inválido: $1"; exit 1; }
for par in "$@"; do
  [[ $par == *=* ]] || error_arg "$par (se espera NOMBRE=valor)"
  nombre=${par%%=*}; valor=${par#*=}
  case "$nombre" in
    VUS|DURACION_S|CALENTAMIENTO_S|INTENTOS|FALLO_S|TEMP_INICIO|TEMP_MAXIMA)
      [[ $valor =~ ^[0-9]+$ ]] || error_arg "$par (se espera un entero)" ;;
    PAUSA_S|CONCURRENTES|ESPERA_S)
      [[ $valor =~ ^[0-9]+(\.[0-9]+)?$ ]] || error_arg "$par (se espera un número)" ;;
    TIMEOUT)
      # Con unidad obligatoria: k6 lee un número solo como milisegundos (TIMEOUT=8 serían 8 ms)
      [[ $valor =~ ^[0-9]+(\.[0-9]+)?(ms|s|m)$ ]] || error_arg "$par (ejemplo: 8s)" ;;
    FALLO)
      [[ $valor =~ ^(procesador|postgres)\ (ordenada|forzada|reinicio)$ ]] \
        || error_arg "$par (ejemplo: FALLO=\"procesador forzada\")" ;;
    *) error_arg "$nombre no es una variable conocida (VUS, DURACION_S, CALENTAMIENTO_S, PAUSA_S, CONCURRENTES, INTENTOS, ESPERA_S, TIMEOUT, FALLO, FALLO_S, TEMP_INICIO, TEMP_MAXIMA)" ;;
  esac
  case "$nombre" in
    FALLO)       fallo=$valor ;;
    FALLO_S)     fallo_s=$valor ;;
    TEMP_INICIO) TEMP_INICIO=$valor ;;
    TEMP_MAXIMA) TEMP_MAXIMA=$valor ;;
    *)           v[$nombre]=$valor ;;
  esac
done
calentamiento=${v[CALENTAMIENTO_S]}; duracion=${v[DURACION_S]}
vars_k6=(); resueltos=""
for nombre in "${orden_k6[@]}"; do
  vars_k6+=(-e "$nombre=${v[$nombre]}"); resueltos+="$nombre=${v[$nombre]} "
done
if [ -n "$fallo" ] && [ "$fallo_s" -ge "$duracion" ]; then
  msg "AVISO: FALLO_S=$fallo_s no cae dentro de la medición (DURACION_S=$duracion)"
fi

# Relativo a la raíz: k6 corre en un contenedor con el repo montado en /repo
rel="resultados/$prueba/$(date +%Y-%m-%d_%H-%M-%S)"
dir="$RAIZ/$rel"
mkdir -p "$dir"

verificar_sensor || true   # avisa una vez si no hay protección térmica
"$RAIZ/scripts/reiniciar_datos.sh"
# Justo antes de medir: vaciar la BD o escalar también calientan el equipo
esperar_frio
"$RAIZ/scripts/condiciones.sh" "$dir/condiciones.txt"

# Código que se probó: -dirty si hay cambios sin commit en archivos versionados
commit=$(git -C "$RAIZ" rev-parse --short HEAD 2>/dev/null || echo desconocido)
if [ "$commit" != desconocido ] && ! git -C "$RAIZ" diff --quiet HEAD -- 2>/dev/null; then commit+="-dirty"; fi
{
  echo "prueba: $prueba"
  echo "argumentos: $*"
  echo "idempotencia: $(k exec deploy/procesador -- printenv IDEMPOTENCIA 2>/dev/null || echo on)"
  echo "replicas_procesador: $(replicas procesador)"
  echo "valores: $resueltos"
  [ -z "$fallo" ] || echo "fallo: $fallo a los $fallo_s s de medición"
  echo "temp_inicio_c: $TEMP_INICIO"
  echo "temp_maxima_c: $TEMP_MAXIMA"
  echo "recursos_api: $(recursos_vivos api)"
  echo "recursos_procesador: $(recursos_vivos procesador)"
  echo "imagen_api: $(imagen_viva api)"
  echo "imagen_procesador: $(imagen_viva procesador)"
  echo "commit: $commit"
  echo "api_keepalive: $(k exec deploy/api -- printenv API_KEEPALIVE 2>/dev/null || echo desconocido)"
  echo "imagen_k6: $K6_IMAGEN"
} > "$dir/parametros.txt"

# ---- Procesos de fondo: todos entran a pids para limpiarlos al salir ----
pids=()
cpu_pid=""; fallo_pid=""
detener() { kill "${pids[@]}" 2>/dev/null || true; wait "${pids[@]}" 2>/dev/null || true; }
# Ctrl+C o kill: se anota en parametros.txt, se detiene k6 por su nombre y se limpia.
# (Los procesos de fondo de un script ignoran Ctrl+C, por eso se detienen aquí.)
interrumpida() {
  trap - INT TERM EXIT
  echo "interrumpida: $(ahora)" >> "$dir/parametros.txt"
  docker stop -t 1 "$K6_CONTENEDOR" >/dev/null 2>&1 || true
  detener
  msg "Corrida interrumpida; resultados parciales en $rel"
  exit 130
}
trap detener EXIT
trap interrumpida INT TERM

# Se redirige stderr de los dos primeros para que sus avisos de parada no ensucien la salida
"$RAIZ/scripts/capturar_recursos.sh" "$dir/recursos.csv" 15 2>/dev/null & pids+=($!)
"$RAIZ/scripts/vigilar_pods.sh" "$dir/pods.csv" 2>/dev/null & pids+=($!)

# Vigía térmico: detiene el contenedor de k6 (por nombre) si la CPU llega a TEMP_MAXIMA.
# Sin sensor termina en silencio: verificar_sensor ya avisó al principio
( echo "marca_tiempo,temperatura_c" > "$dir/temperatura.csv"
  while sleep 2; do
    t=$(temperatura); [ -n "$t" ] || exit 0
    echo "$(ahora),$t" >> "$dir/temperatura.csv"
    if [ "$t" -ge "$TEMP_MAXIMA" ]; then
      msg "ABORTADA: CPU a ${t} °C (límite ${TEMP_MAXIMA} °C); se detiene k6"
      echo "abortada: CPU a ${t} °C a las $(ahora)" >> "$dir/parametros.txt"
      docker stop -t 1 "$K6_CONTENEDOR" >/dev/null 2>&1 || true
      exit 0
    fi
  done ) &
pids+=($!)

# ---- k6 en segundo plano, para contar las ventanas desde que de verdad corre ----
inicio_registros=$(date -u +%Y-%m-%dT%H:%M:%SZ)
msg "Corriendo k6 ($prueba): ${*:-valores por defecto}"
docker rm -f "$K6_CONTENEDOR" >/dev/null 2>&1 || true   # uno viejo confundiría la espera de abajo
( "$RAIZ/scripts/k6.sh" run -q "${vars_k6[@]}" \
    -e RESUMEN="$rel/resumen.json" \
    --out csv="$rel/k6.csv.gz" \
    --log-format=raw --console-output="$rel/claves.jsonl" \
    scripts/k6_jugadores.js | tee "$dir/resumen.txt" ) &
k6_pid=$!; pids+=($k6_pid)

# Espera (máx. 60 s) a que el contenedor esté "running"; 1 si k6 murió antes de arrancar
k6_corriendo() {
  local fin=$(( $(date +%s) + 60 ))
  while [ "$(date +%s)" -lt "$fin" ]; do
    [ "$(docker inspect -f '{{.State.Running}}' "$K6_CONTENEDOR" 2>/dev/null)" = true ] && return 0
    kill -0 "$k6_pid" 2>/dev/null || return 1
    sleep 0.1
  done
  return 1
}

if k6_corriendo; then
  inicio_k6=$(date +%s)
  # Contadores de CPU justo al empezar y al terminar la medición
  ( sleep "$calentamiento"; "$RAIZ/scripts/cpu_pods.sh" inicio "$dir/cpu_contadores.csv"
    sleep "$duracion";      "$RAIZ/scripts/cpu_pods.sh" fin "$dir/cpu_contadores.csv" ) &
  cpu_pid=$!; pids+=($cpu_pid)

  if [ -n "$fallo" ]; then
    # shellcheck disable=SC2086  # FALLO lleva dos palabras: objetivo y modo
    ( sleep $((calentamiento + fallo_s)); "$RAIZ/scripts/eliminar_pod.sh" $fallo "$dir/fallos.csv" >/dev/null ) &
    fallo_pid=$!; pids+=($fallo_pid)
    msg "Fallo programado: $fallo a los $fallo_s s de medición"
  fi
else
  msg "AVISO: el contenedor $K6_CONTENEDOR no arrancó; no se miden CPU ni fallo"
  # Queda en los resultados: una corrida de fallo sin fallo no debe pasar por válida
  echo "sin_medir: k6 no quedó corriendo en 60 s; no se leyó la CPU ni se provocó el fallo" >> "$dir/parametros.txt"
fi

codigo=0
wait "$k6_pid" || codigo=$?
[ "$codigo" -eq 0 ] || msg "AVISO: k6 terminó con código $codigo"

# Si k6 terminó antes de tiempo (aborto por calor o error), no tiene caso esperar la
# lectura final de CPU, y el fallo que todavía no ocurrió se cancela: provocarlo
# borraría un pod con la medición ya detenida. Los márgenes de 1 y 2 s evitan
# cancelar por redondeo en una corrida que terminó a tiempo
cancelar() { pkill -P "$1" 2>/dev/null || true; kill "$1" 2>/dev/null || true; }
abortada=false; grep -q '^abortada' "$dir/parametros.txt" && abortada=true
transcurrido=$(( $(date +%s) - ${inicio_k6:-0} ))
if [ -n "$fallo_pid" ] && { $abortada || [ "$transcurrido" -lt $((calentamiento + fallo_s - 1)) ]; } \
   && [ "$transcurrido" -lt $((calentamiento + fallo_s)) ]; then
  cancelar "$fallo_pid"; fallo_pid=""
  echo "fallo_cancelado: k6 terminó a los $transcurrido s, antes del fallo" >> "$dir/parametros.txt"
fi
if [ -n "$cpu_pid" ] && { $abortada || [ "$transcurrido" -lt $((calentamiento + duracion - 2)) ]; }; then
  cancelar "$cpu_pid"; cpu_pid=""
fi
# Si el fallo sigue en curso (pod aún no listo) se espera antes de copiar la BD
if [ -n "$fallo_pid" ]; then wait "$fallo_pid" 2>/dev/null || true; fi
if [ -n "$cpu_pid" ]; then wait "$cpu_pid" 2>/dev/null || true; fi
detener; trap - EXIT
# Los errores de resumir_cpu.py se muestran; sin lectura final avisa y no falla
python3 "$RAIZ/scripts/resumir_cpu.py" "$dir" | tee -a "$dir/resumen.txt" \
  || msg "AVISO: resumir_cpu.py falló; revisa cpu_contadores.csv"
awk -F, 'NR>1 {if ($2>m) m=$2; n++} END {if (n) print "Temperatura máxima de la CPU: " m " °C"}' \
  "$dir/temperatura.csv" | tee -a "$dir/resumen.txt"

# Registros de los pods que siguen vivos (los de un pod eliminado se pierden con él)
k logs -l app=api --prefix --tail=-1 --since-time="$inicio_registros" 2>/dev/null | gzip > "$dir/api.log.gz"
k logs -l app=procesador --prefix --tail=-1 --max-log-requests=10 --since-time="$inicio_registros" 2>/dev/null \
  | gzip > "$dir/procesador.log.gz"

"$RAIZ/scripts/verificar.sh" "$dir" | tee -a "$dir/resumen.txt"
gzip "$dir/claves.jsonl"
msg "Resultados en $rel"
