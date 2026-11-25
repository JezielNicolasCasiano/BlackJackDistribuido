#!/usr/bin/env bash
# Uso: eliminar_pod.sh ordenada|forzada [ARCHIVO.csv]
# Elimina un pod del procesador (E4, fallo de instancia) y mide cuánto tarda
# Kubernetes en tener otra vez todas las réplicas listas (diseño, 8.4).
#   ordenada -> kubectl delete pod (SIGTERM y hasta 30 s de gracia)
#   forzada  -> --grace-period=0 --force (caída abrupta)
# La marca de tiempo de la eliminación sirve para alinear con la serie de k6.
set -euo pipefail
source "$(dirname "$0")/comun.sh"
modo=${1:?Uso: eliminar_pod.sh ordenada|forzada [ARCHIVO.csv]}; archivo=${2:-}

replicas=$(k get deploy procesador -o jsonpath='{.spec.replicas}')
victima=$(k get pods -l app=procesador -o jsonpath='{.items[0].metadata.name}')
case "$modo" in
  ordenada) opciones=(--wait=false) ;;
  forzada)  opciones=(--wait=false --grace-period=0 --force) ;;
  *) msg "Modo inválido: $modo"; exit 1 ;;
esac

listos() {  # pods del procesador listos, sin contar a la víctima
  k get pods -l app=procesador -o jsonpath='{range .items[*]}{.metadata.name} {.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' \
    | awk -v v="$victima" '$1 != v && $2 == "True" {print $1}'
}

t0=$(date +%s.%N); marca=$(ahora)
k delete pod "$victima" "${opciones[@]}" >/dev/null 2>&1
msg "Eliminado $victima ($modo) a las $marca"
until [ "$(listos | wc -l)" -ge "$replicas" ]; do sleep 0.25; done
segundos=$(python3 -c "print(f'{$(date +%s.%N) - $t0:.2f}')")
msg "Réplicas listas otra vez tras $segundos s"

if [ -n "$archivo" ]; then
  mkdir -p "$(dirname "$archivo")"
  [ -s "$archivo" ] || echo "marca_eliminacion,modo,pod_eliminado,replicas,segundos_hasta_listo" > "$archivo"
  echo "$marca,$modo,$victima,$replicas,$segundos" >> "$archivo"
fi
echo "$segundos"
