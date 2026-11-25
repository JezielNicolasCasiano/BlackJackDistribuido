#!/usr/bin/env bash
# Uso: condiciones.sh ARCHIVO.txt
# Registra las condiciones del equipo antes de una prueba (diseño, 9.1):
# hardware, temperatura, carga, procesos pesados, versiones y configuración.
# Avisa si el equipo no está en condiciones de medir (prueba de despliegue, sección 10).
set -uo pipefail
source "$(dirname "$0")/comun.sh"
archivo=${1:?Uso: condiciones.sh ARCHIVO.txt}
mkdir -p "$(dirname "$archivo")"

temp=$(sensors 2>/dev/null | awk '/Package id 0/ {gsub(/[+°C]/,"",$4); print $4; exit}')
carga=$(cut -d' ' -f1 /proc/loadavg)
{
  echo "fecha: $(ahora)"
  echo "cpu: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs) ($(nproc) núcleos)"
  echo "ram_total: $(free -g | awk '/Mem:/ {print $2}') GB"
  echo "kernel: $(uname -r)"
  echo "temperatura_cpu_c: ${temp:-desconocida}"
  echo "carga_1min: $carga"
  echo "docker: $(docker version --format '{{.Server.Version}}' 2>/dev/null)"
  echo "minikube: $(minikube version --short 2>/dev/null)"
  echo "kubectl_cliente: $(kubectl version --client -o json 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["clientVersion"]["gitVersion"])')"
  echo "kubernetes_servidor: $(kubectl version -o json 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["serverVersion"]["gitVersion"])')"
  echo "k6: $(docker image ls grafana/k6 --format '{{.Tag}}' 2>/dev/null | head -1)"
  echo "imagenes: $(k get deploy,statefulset -o jsonpath='{range .items[*]}{.spec.template.spec.containers[0].image} {end}')"
  echo "replicas_procesador: $(k get deploy procesador -o jsonpath='{.spec.replicas}')"
  echo "configmap: $(k get cm blackjack-config -o jsonpath='{.data}')"
  echo "procesos_con_mas_cpu:"
  ps -eo pcpu,comm --sort=-pcpu | sed -n '2,6p' | sed 's/^/  /'
} > "$archivo"

# Avisos: no impiden medir, pero quedan registrados
avisos=0
if [ -n "$temp" ] && [ "${temp%.*}" -ge 75 ]; then msg "AVISO: CPU a ${temp} °C"; avisos=1; fi
if awk -v c="$carga" 'BEGIN{exit !(c > 2)}'; then msg "AVISO: carga de $carga antes de empezar; ¿hay otros procesos pesados?"; avisos=1; fi
[ "$avisos" -eq 0 ] && msg "Condiciones registradas en $archivo" || echo "avisos: sí" >> "$archivo"
