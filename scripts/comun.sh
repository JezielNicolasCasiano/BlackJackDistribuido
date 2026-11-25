# Funciones compartidas por los scripts de los experimentos.
# Se carga con: source "$(dirname "$0")/comun.sh"

NS=blackjack
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ahora() { date +%Y-%m-%dT%H:%M:%S.%3N%:z; }          # marca de tiempo con milisegundos
msg()   { echo "[$(date +%T)] $*" >&2; }
k()     { kubectl -n "$NS" "$@"; }
api_url() { echo "http://$(minikube ip):30080"; }

# psql dentro del pod de PostgreSQL, con las credenciales del Secret
psql_bd() { k exec -i postgres-0 -- sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -qtA "$@"' psql "$@"; }

esperar_pods_listos() { k wait --for=condition=Ready pod --all --timeout="${1:-180s}" >/dev/null; }

# Que el pod aparezca como listo no garantiza que la BD acepte conexiones
# (prueba de despliegue, sección 6), por eso se comprueba con una consulta real.
esperar_bd() {
  for _ in $(seq 60); do
    psql_bd -c "SELECT 1" >/dev/null 2>&1 && return 0
    sleep 1
  done
  msg "ERROR: la base de datos no respondió en 60 s"; return 1
}

# Cinco respuestas seguidas de la cadena completa equipo -> API -> procesador
esperar_cadena() {
  local url ok=0; url=$(api_url)
  for _ in $(seq 120); do
    if curl -sf -m 2 "$url/quien" >/dev/null; then ok=$((ok+1)); else ok=0; fi
    [ "$ok" -ge 5 ] && return 0
    sleep 0.5
  done
  msg "ERROR: la cadena completa no respondió"; return 1
}
