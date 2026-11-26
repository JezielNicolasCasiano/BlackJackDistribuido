# Funciones compartidas por los scripts

NS=blackjack
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Valores que antes estaban repetidos en varios scripts. Los dos primeros
# umbrales se pueden cambiar desde el entorno (TEMP_INICIO=55 jugar.sh ...)
K6_IMAGEN=grafana/k6:2.4.0
K6_CONTENEDOR=k6-blackjack   # nombre fijo: el vigía térmico lo detiene por nombre
TEMP_INICIO=${TEMP_INICIO:-60}   # se espera a que la CPU baje de aquí antes de medir
TEMP_MAXIMA=${TEMP_MAXIMA:-90}   # a esta temperatura se aborta la corrida
TEMP_AVISO=75                    # condiciones.sh avisa a partir de aquí
CARGA_AVISO=2                    # carga de 1 min a partir de la cual se avisa

ahora()   { date +%Y-%m-%dT%H:%M:%S.%3N%:z; }
msg()     { echo "[$(date +%T)] $*" >&2; }
k()       { kubectl -n "$NS" "$@"; }
api_url() { echo "http://$(minikube ip):30080"; }

# psql dentro del pod de PostgreSQL
psql_bd() { k exec -i postgres-0 -- sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -qtA "$@"' psql "$@"; }

# ---- Utilidades sin clúster ----

# Segundos (con decimales) transcurridos desde INICIO, que viene de `date +%s.%N`.
# LC_ALL=C para que awk imprima punto decimal aunque el sistema use coma
segundos_desde() { LC_ALL=C awk -v t0="$1" -v t1="$(date +%s.%N)" 'BEGIN {printf "%.2f\n", t1 - t0}'; }

# Crea ARCHIVO (y su carpeta) con ENCABEZADO si no existe o está vacío; si ya
# tiene contenido, lo deja para seguir agregando filas
csv_con_encabezado() {
  mkdir -p "$(dirname "$1")"
  [ -s "$1" ] || echo "$2" > "$1"
}

# Repite un comando (puede ser una función) cada 0.25 s hasta que tenga éxito o
# pasen LIMITE_S segundos. Evita bucles `until` que se cuelgan si algo no ocurre
con_limite() {
  local limite=$1; shift
  local fin=$(( $(date +%s) + limite ))
  until "$@"; do
    [ "$(date +%s)" -lt "$fin" ] || return 1
    sleep 0.25
  done
}

# ---- Frecuencia de la CPU ----

# Verdadero si la frecuencia mínima está fijada en la máxima. Con el gobernador
# powersave la frecuencia baja cuando el equipo tiene poca carga, y un procesador
# limitado a 100m es poca carga: medido, una réplica saturada dio 21.6-24.2
# solicitudes/s con la frecuencia libre y 32.8 con la frecuencia fija en 2.4 GHz
frecuencia_fija() {
  local f=/sys/devices/system/cpu/cpu0/cpufreq
  [ -r "$f/scaling_min_freq" ] || return 0   # sin cpufreq no hay nada que fijar
  [ "$(cat "$f/scaling_min_freq")" = "$(cat "$f/scaling_max_freq")" ]
}
comando_fijar_frecuencia() {
  echo "echo $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq) | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_min_freq"
}

# ---- Temperatura ----

# Temperatura del paquete de la CPU con decimales ("62.0"); vacío si no hay
# sensor "Package id 0" (CPU AMD o sin lm_sensors). Es la única lectura: la
# versión entera sale de ella para que todos los scripts coincidan
temperatura_decimal() {
  sensors 2>/dev/null | awk '/Package id 0/ {if (match($4, /[0-9]+(\.[0-9]+)?/)) {print substr($4, RSTART, RLENGTH); exit}}'
}
temperatura() { local t; t=$(temperatura_decimal); echo "${t%.*}"; }

# Sin sensor no hay protección térmica; se avisa en voz alta en lugar de
# desactivarla en silencio. Devuelve 1 si no hay sensor
verificar_sensor() {
  [ -n "$(temperatura_decimal)" ] && return 0
  msg "AVISO: no hay sensor 'Package id 0' (¿CPU AMD o falta lm_sensors?). Sin protección térmica: vigila la temperatura a mano"
  return 1
}

# El equipo se apagó por calor varias veces durante las pruebas: antes de cada
# corrida se espera a que la CPU baje de TEMP_INICIO
esperar_frio() {
  local t; t=$(temperatura); [ -n "$t" ] || return 0
  [ "$t" -lt "$TEMP_INICIO" ] && return 0
  msg "CPU a ${t} °C; esperando a que baje de ${TEMP_INICIO} °C"
  while t=$(temperatura); [ -n "$t" ] && [ "$t" -ge "$TEMP_INICIO" ]; do sleep 5; done
  msg "CPU a ${t} °C"
}

# ---- Clúster ----

# Espera a cada Deployment y StatefulSet, no a una lista de pods: kubectl wait
# toma la lista al empezar y se cuelga con pods que se están borrando (al bajar
# réplicas) o con el pod de un Job terminado, que nunca queda Ready
esperar_pods_listos() {
  local obj
  for obj in $(k get deploy,statefulset -o name); do
    k rollout status "$obj" --timeout="${1:-180s}" >/dev/null || return 1
  done
}

# Pod listo no implica BD lista, por eso se prueba una consulta real
esperar_bd() {
  for _ in $(seq 60); do
    psql_bd -c "SELECT 1" >/dev/null 2>&1 && return 0
    sleep 1
  done
  msg "ERROR: la base de datos no respondió en 60 s"; return 1
}

# 5 respuestas seguidas de API -> procesador
esperar_cadena() {
  local url ok=0; url=$(api_url)
  for _ in $(seq 120); do
    if curl -sf -m 2 "$url/quien" >/dev/null; then ok=$((ok+1)); else ok=0; fi
    [ "$ok" -ge 5 ] && return 0
    sleep 0.5
  done
  msg "ERROR: la cadena completa no respondió"; return 1
}

# Número de réplicas pedidas de un Deployment
replicas() { k get deploy "$1" -o jsonpath='{.spec.replicas}'; }

# Espera a que el despliegue de DEPLOY termine y a que no queden pods viejos:
# el preStop los deja vivos unos segundos y rollout status ya no los cuenta
esperar_rollout() {
  local deploy=$1
  k rollout status "deployment/$deploy" --timeout="${2:-180s}" >/dev/null || return 1
  # La cuenta se vuelve a consultar en cada intento, por eso va en una función
  sin_pods_viejos() { [ "$(k get pods -l app="$deploy" --no-headers | wc -l)" -eq "$(replicas "$deploy")" ]; }
  con_limite 60 sin_pods_viejos || { msg "ERROR: siguen pods viejos de $deploy tras 60 s"; return 1; }
}

# Manifiesto de cada pieza propia
manifiesto_de() {
  case "$1" in
    api)        echo "$RAIZ/k8s/40-api.yaml" ;;
    procesador) echo "$RAIZ/k8s/30-procesador.yaml" ;;
    *) return 1 ;;
  esac
}

# Imagen del primer contenedor declarada en un manifiesto
imagen_de() { grep -m1 -o 'image: [^ ]*' "$1" | cut -d' ' -f2; }

# Imagen y recursos del Deployment vivo (lo que de verdad corre, no el archivo)
imagen_viva() { k get deploy "$1" -o jsonpath='{.spec.template.spec.containers[0].image}'; }
recursos_vivos() {
  k get deploy "$1" -o jsonpath='requests cpu={.spec.template.spec.containers[0].resources.requests.cpu} memoria={.spec.template.spec.containers[0].resources.requests.memory}; limits cpu={.spec.template.spec.containers[0].resources.limits.cpu} memoria={.spec.template.spec.containers[0].resources.limits.memory}'
}
