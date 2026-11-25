#!/usr/bin/env bash
# Deja el entorno listo para experimentar. Se corre después de cada arranque
# del equipo, porque minikube start deshace algunos ajustes.
#   1. Arranca Minikube con la configuración de los experimentos (diseño, 9.1)
#   2. Baja la resolución de metrics-server de 60 s a 15 s
#   3. Construye las imágenes que falten dentro de Minikube
#   4. Aplica los manifiestos y fija el namespace blackjack
#   5. Espera a que la BD y la cadena completa respondan
set -euo pipefail
source "$(dirname "$0")/comun.sh"

msg "Arrancando Minikube (4 CPU, 8 GB)"
minikube start --driver=docker --cpus=4 --memory=8g >/dev/null
minikube addons enable metrics-server >/dev/null

# metrics-server trae --metric-resolution=60s: en una prueba de 2 minutos solo
# habría 2 mediciones de CPU. minikube start lo regresa a 60 s cada vez.
args=$(kubectl -n kube-system get deploy metrics-server -o json | python3 -c '
import json, sys
a = json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0]["args"]
a = [x for x in a if not x.startswith("--metric-resolution")] + ["--metric-resolution=15s"]
print(json.dumps(a))')
kubectl -n kube-system patch deploy metrics-server --type=json \
  -p="[{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/args\",\"value\":$args}]" >/dev/null
kubectl -n kube-system rollout status deploy/metrics-server --timeout=120s >/dev/null
msg "metrics-server con resolución de 15 s"

# Imágenes: se construyen solo si no existen, con la etiqueta que piden los manifiestos
eval "$(minikube docker-env)"
for pieza in api procesador; do
  imagen=$(grep -ho "image: blackjack-$pieza:[^ ]*" "$RAIZ"/k8s/*.yaml | head -1 | cut -d' ' -f2)
  if docker image inspect "$imagen" >/dev/null 2>&1; then
    msg "Imagen $imagen ya existe"
  else
    msg "Construyendo $imagen"; docker build -q -t "$imagen" "$RAIZ/$pieza" >/dev/null
  fi
done

kubectl apply -f "$RAIZ/k8s/" >/dev/null
kubectl config set-context --current --namespace="$NS" >/dev/null   # minikube start lo regresa a default
msg "Manifiestos aplicados; esperando a que todo responda"
esperar_pods_listos 300s
esperar_bd
esperar_cadena
msg "Entorno listo en $(api_url)"
