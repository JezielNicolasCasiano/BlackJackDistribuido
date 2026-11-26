# Funciones compartidas por los scripts

NS=blackjack
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ahora() { date +%Y-%m-%dT%H:%M:%S.%3N%:z; }
msg()   { echo "[$(date +%T)] $*" >&2; }
k()     { kubectl -n "$NS" "$@"; }
api_url() { echo "http://$(minikube ip):30080"; }

# psql dentro del pod de PostgreSQL
psql_bd() { k exec -i postgres-0 -- sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -qtA "$@"' psql "$@"; }

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

# Temperatura del paquete de la CPU en grados enteros (vacío si no hay sensores)
temperatura() { sensors 2>/dev/null | awk '/Package id 0/ {gsub(/[+°C]/,"",$4); split($4,a,"."); print a[1]; exit}'; }

# El equipo se apagó por calor varias veces durante las pruebas: antes de cada
# corrida se espera a que la CPU baje de TEMP_INICIO (60 °C por defecto)
esperar_frio() {
  local t limite=${TEMP_INICIO:-60}
  t=$(temperatura); [ -n "$t" ] || return 0
  [ "$t" -lt "$limite" ] && return 0
  msg "CPU a ${t} °C; esperando a que baje de ${limite} °C"
  while t=$(temperatura); [ "$t" -ge "$limite" ]; do sleep 5; done
  msg "CPU a ${t} °C"
}
