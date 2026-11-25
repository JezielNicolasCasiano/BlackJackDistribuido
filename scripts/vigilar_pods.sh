#!/usr/bin/env bash
# Uso: vigilar_pods.sh ARCHIVO.csv
# Registra cada cambio de estado de los pods con su hora exacta. kubectl get
# pods -w muestra los cambios, pero sin la hora, que es lo que hace falta para
# medir el tiempo de recuperación (diseño, 9.4). Corre hasta que se detiene:
#   scripts/vigilar_pods.sh resultados/e4/.../pods.csv & PID=$!; ...; kill $PID
# Columnas: hora del cambio, pod, fase, si está listo y si está en eliminación.
set -uo pipefail
source "$(dirname "$0")/comun.sh"
archivo=${1:?Uso: vigilar_pods.sh ARCHIVO.csv}
mkdir -p "$(dirname "$archivo")"
[ -s "$archivo" ] || echo "marca_tiempo,pod,fase,listo,en_eliminacion" > "$archivo"

# kubectl se lanza directo en segundo plano y su salida pasa al ciclo de
# lectura por sustitución de procesos. Así $! es el PID de kubectl: al
# matarlo, el ciclo recibe fin de archivo y termina solo. Con una tubería
# (kubectl | while ... &) kubectl quedaría como nieto del script y sobreviviría
# como huérfano. Tampoco sirve kill 0, que alcanzaría a todo el grupo de
# procesos, incluido este script y el que lo lanzó. Por la misma razón se
# llama a kubectl directamente y no a la función k de comun.sh: una función en
# segundo plano corre en un subshell, y $! sería el subshell y no kubectl.
kubectl -n "$NS" get pods -w -o jsonpath='{.metadata.name},{.status.phase},{.status.conditions[?(@.type=="Ready")].status},{.metadata.deletionTimestamp}{"\n"}' \
  > >(while IFS=, read -r pod fase listo borrado; do
        echo "$(ahora),$pod,$fase,${listo:-False},$([ -n "$borrado" ] && echo sí || echo no)"
      done >> "$archivo") &
kpid=$!
trap 'trap - INT TERM; kill "$kpid" 2>/dev/null; wait "$kpid" 2>/dev/null; msg "Vigilancia detenida ($archivo)"; exit 0' INT TERM
wait "$kpid"
