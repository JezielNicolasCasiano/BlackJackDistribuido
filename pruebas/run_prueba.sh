#!/usr/bin/env bash
# Uso: pruebas/run_prueba.sh PRUEBA REPLICAS VUS DURACION [FALLO] [P_DOBLE]
#
# Corre UNA repetición de un experimento (sección 9.1 del diseño) con los scripts del
# equipo y guarda todo en pruebas/resultados/<PRUEBA>/<fecha-hora>-r<R>-v<VUS>-<FALLO>/
#
#   PRUEBA   etiqueta de la carpeta: e1, e2, e3, e4, e5, control
#   FALLO    ninguno (por omisión) | procesador-ordenada | procesador-forzada |
#            procesador-reinicio | postgres-ordenada | postgres-forzada | postgres-reinicio
#            (el fallo se provoca a los SEG_FALLO segundos, 60 por omisión, con scripts/eliminar_pod.sh)
#   P_DOBLE  probabilidad (0 a 1) de mandar dos "pedir" simultáneos sobre la misma partida (E3)
#
# Ejemplos (desde la raíz del repo, con el entorno ya levantado por scripts/preparar_entorno.sh):
#   pruebas/run_prueba.sh e1 1 10 2m
#   pruebas/run_prueba.sh e2 3 40 2m
#   pruebas/run_prueba.sh e3 2 50 1m ninguno 0.3
#   pruebas/run_prueba.sh e4 3 40 3m procesador-forzada
#   pruebas/run_prueba.sh e5 2 40 3m procesador-reinicio
#   pruebas/run_prueba.sh e5 2 40 3m postgres-forzada
#   IDEMPOTENCIA=off pruebas/run_prueba.sh control 2 40 3m procesador-reinicio   # control (9.3)
#
# Con IDEMPOTENCIA=on|off en el entorno se llama a scripts/idempotencia.sh; al terminar una
# corrida con off se vuelve a on.
set -euo pipefail
source "$(dirname "$0")/../scripts/comun.sh"     # NS, RAIZ, k, msg, ahora, api_url, esperar_bd

PRUEBA=${1:?Uso: run_prueba.sh PRUEBA REPLICAS VUS DURACION [FALLO] [P_DOBLE]}
REPLICAS=${2:?falta REPLICAS}; VUS=${3:?falta VUS}; DURACION=${4:?falta DURACION}
FALLO=${5:-ninguno}; P_DOBLE=${6:-0}
SEG_FALLO=${SEG_FALLO:-60}

[[ "$FALLO" =~ ^(ninguno|(procesador|postgres)-(ordenada|forzada|reinicio))$ ]] \
  || { msg "FALLO inválido: $FALLO"; sed -n '8,10p' "$0"; exit 1; }

REL="pruebas/resultados/$PRUEBA/$(date +%Y%m%d-%H%M%S)-r${REPLICAS}-v${VUS}-${FALLO}"
DIR="$RAIZ/$REL"; mkdir -p "$DIR"
S="$RAIZ/scripts"
cd "$RAIZ"          # k6.sh monta el repo en /repo: las rutas de k6 son relativas a la raíz
msg "Resultados en $REL"

PIDS=()
limpiar() {
  [ "${#PIDS[@]}" -gt 0 ] && kill "${PIDS[@]}" 2>/dev/null || true
  if [ "${IDEMPOTENCIA:-}" = off ]; then "$S/idempotencia.sh" on || true; fi
}
trap limpiar EXIT

# ---- Preparación: réplicas, modo de idempotencia, condiciones, datos limpios ----
"$S/escalar.sh" "$REPLICAS"
[ -n "${IDEMPOTENCIA:-}" ] && "$S/idempotencia.sh" "$IDEMPOTENCIA"
"$S/condiciones.sh" "$DIR/condiciones.txt"
{
  echo "prueba=$PRUEBA replicas=$REPLICAS vus=$VUS duracion=$DURACION fallo=$FALLO"
  echo "p_doble=$P_DOBLE seg_fallo=$SEG_FALLO idempotencia=${IDEMPOTENCIA:-(sin cambiar)}"
} > "$DIR/config.txt"

# ---- Calentamiento de 30 s que no se cuenta (9.1): se corre y se borran sus datos ----
msg "Calentamiento de 30 s"
"$S/reiniciar_datos.sh"
"$S/k6.sh" run --quiet -e VUS="$VUS" -e DURACION=30s pruebas/k6/blackjack.js >/dev/null 2>&1 || true
"$S/reiniciar_datos.sh"

# ---- Monitores en segundo plano ----
"$S/capturar_recursos.sh" "$DIR/recursos.csv" 15 & PIDS+=($!)
"$S/vigilar_pods.sh" "$DIR/pods.csv" & PIDS+=($!)

# ---- Fallo programado (usa scripts/eliminar_pod.sh, que también mide la recuperación) ----
PID_FALLO=""
if [ "$FALLO" != ninguno ]; then
  ( sleep "$SEG_FALLO"; "$S/eliminar_pod.sh" "${FALLO%-*}" "${FALLO#*-}" "$DIR/fallos.csv" >/dev/null ) &
  PID_FALLO=$!
fi

# ---- La prueba. k6.json.gz: el JSON sin comprimir pesa cientos de MB ----
msg "Corriendo k6: $VUS VUs, $DURACION, fallo=$FALLO"
"$S/k6.sh" run -e VUS="$VUS" -e DURACION="$DURACION" -e P_DOBLE="$P_DOBLE" \
  --out json="$REL/k6.json.gz" --summary-export="$REL/resumen_k6.json" \
  pruebas/k6/blackjack.js 2>&1 | tee "$DIR/k6_salida.txt" || true
[ -n "$PID_FALLO" ] && { wait "$PID_FALLO" || true; }
sleep 2; kill "${PIDS[@]}" 2>/dev/null || true; PIDS=()

# ---- Verificación de integridad (R5 a R8) y comparación con lo que vio k6 ----
esperar_bd
if [ -z "${SIN_PORT_FORWARD:-}" ]; then
  secreto() { k get secret postgres-credenciales -o jsonpath="{.data.$1}" | base64 -d; }
  export PGUSER=$(secreto POSTGRES_USER) PGPASSWORD=$(secreto POSTGRES_PASSWORD) PGDATABASE=$(secreto POSTGRES_DB)
  export PGHOST=127.0.0.1 PGPORT=55433
  kubectl -n "$NS" port-forward pod/postgres-0 "$PGPORT:5432" >/dev/null 2>&1 & PIDS+=($!)
  sleep 3
fi
PY="$RAIZ/.venv/bin/python"; [ -x "$PY" ] || PY=python3
"$PY" "$RAIZ/pruebas/verificar_integridad.py" --k6-json "$DIR/k6.json.gz" \
    --salida "$DIR/verificacion.json" | tee "$DIR/verificacion_salida.txt" \
  || msg ">>> HAY VIOLACIONES DE INTEGRIDAD U OPERACIONES PERDIDAS/DUPLICADAS: revisar $REL/verificacion.json"
msg "Listo: $REL"
