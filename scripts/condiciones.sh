#!/usr/bin/env bash
# Uso: condiciones.sh ARCHIVO.txt
# Registra el estado del equipo antes de una prueba y avisa si no conviene medir
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
  echo "turbo: $([ "$(cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null)" = 1 ] && echo apagado || echo encendido)"
  echo "frecuencia_max_mhz: $(( $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq 2>/dev/null || echo 0) / 1000 ))"
  echo "perfil_energia: $(powerprofilesctl get 2>/dev/null || echo desconocido)"
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

# Los avisos no detienen la prueba
avisos=0
if [ -n "$temp" ] && [ "${temp%.*}" -ge 75 ]; then msg "AVISO: CPU a ${temp} °C"; avisos=1; fi
if [ "$(cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null)" = 0 ]; then msg "AVISO: turbo encendido (las mediciones dependen de la temperatura)"; avisos=1; fi
if awk -v c="$carga" 'BEGIN{exit !(c > 2)}'; then msg "AVISO: carga de $carga antes de empezar; ¿hay otros procesos pesados?"; avisos=1; fi
[ "$avisos" -eq 0 ] && msg "Condiciones registradas en $archivo" || echo "avisos: sí" >> "$archivo"
