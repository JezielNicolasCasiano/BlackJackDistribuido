#!/usr/bin/env bash
# Prepara Minikube, imágenes y manifiestos. Correr tras cada arranque del equipo.
set -euo pipefail
source "$(dirname "$0")/comun.sh"

msg "Arrancando Minikube (4 CPU, 8 GB)"
minikube start --driver=docker --cpus=4 --memory=8g >/dev/null
minikube addons enable metrics-server >/dev/null

# metrics-server mide cada 60 s, se baja a 15 s (minikube start lo revierte)
args=$(kubectl -n kube-system get deploy metrics-server -o json | python3 -c '
import json, sys
a = json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0]["args"]
a = [x for x in a if not x.startswith("--metric-resolution")] + ["--metric-resolution=15s"]
print(json.dumps(a))')
kubectl -n kube-system patch deploy metrics-server --type=json \
  -p="[{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/args\",\"value\":$args}]" >/dev/null
kubectl -n kube-system rollout status deploy/metrics-server --timeout=120s >/dev/null
msg "metrics-server con resolución de 15 s"

# Construye solo las imágenes que falten
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
