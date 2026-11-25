#!/usr/bin/env bash
# Uso: crear_esquema.sh [--desde-cero]
# Carga bd/esquema.sql en un ConfigMap y lanza el Job que crea las tablas.
# --desde-cero borra antes las tablas del juego (y sus datos), para aplicar cambios al esquema.
set -euo pipefail
source "$(dirname "$0")/comun.sh"

case "${1:-}" in
  "") ;;
  --desde-cero)
    esperar_bd
    psql_bd -c "DROP TABLE IF EXISTS solicitudes, cartas, partidas"
    msg "Tablas del juego borradas" ;;
  *) sed -n '2,4p' "$0"; exit 1 ;;
esac

# El ConfigMap se genera desde el archivo, así el SQL vive en un solo lugar
k create configmap esquema-sql --from-file=esquema.sql="$RAIZ/bd/esquema.sql" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

# Un Job no se puede modificar ni volver a correr: se borra y se crea de nuevo
k delete job esquema --ignore-not-found >/dev/null
kubectl apply -f "$RAIZ/k8s/esquema/job.yaml" >/dev/null
msg "Job esquema lanzado"

# kubectl wait solo espera una condición, y aquí puede terminar bien o mal
for _ in $(seq 200); do
  estado=$(k get job esquema -o jsonpath='{.status.succeeded}/{.status.failed}')
  case "$estado" in
    1/*) break ;;
    */3) msg "ERROR: el Job falló 3 veces"; k logs job/esquema >&2; exit 1 ;;
  esac
  sleep 1
done
[ "${estado%%/*}" = 1 ] || { msg "ERROR: el Job no terminó a tiempo"; k logs job/esquema >&2; exit 1; }

msg "Esquema creado: $(psql_bd -c "SELECT string_agg(tablename, ', ' ORDER BY tablename) FROM pg_tables
  WHERE schemaname = 'public' AND tablename IN ('partidas', 'cartas', 'solicitudes')")"
