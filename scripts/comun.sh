# Funciones compartidas por los scripts

NS=blackjack
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ahora() { date +%Y-%m-%dT%H:%M:%S.%3N%:z; }
msg()   { echo "[$(date +%T)] $*" >&2; }
k()     { kubectl -n "$NS" "$@"; }
api_url() { echo "http://$(minikube ip):30080"; }

# psql dentro del pod de PostgreSQL
psql_bd() { k exec -i postgres-0 -- sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -qtA "$@"' psql "$@"; }

esperar_pods_listos() { k wait --for=condition=Ready pod --all --timeout="${1:-180s}" >/dev/null; }

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
