#!/usr/bin/env bash
# Uso: publicar_version.sh api|procesador VERSION
# Construye, actualiza el manifiesto y despliega. Cada cambio lleva versión nueva.
set -euo pipefail
source "$(dirname "$0")/comun.sh"
pieza=${1:?Uso: publicar_version.sh api|procesador VERSION}; version=${2:?Falta la VERSION, por ejemplo 0.3}

case "$pieza" in
  api)        manifiesto="$RAIZ/k8s/40-api.yaml" ;;
  procesador) manifiesto="$RAIZ/k8s/30-procesador.yaml" ;;
  *) msg "Pieza inválida: $pieza (api o procesador)"; exit 1 ;;
esac
[[ "$version" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || { msg "Versión inválida: $version (ejemplo 0.3)"; exit 1; }

imagen="blackjack-$pieza:$version"
actual=$(grep -o "image: blackjack-$pieza:[^ ]*" "$manifiesto" | cut -d' ' -f2)
eval "$(minikube docker-env)"
if docker image inspect "$imagen" >/dev/null 2>&1; then
  msg "ERROR: $imagen ya existe. Usa una versión nueva (la actual es $actual)."; exit 1
fi

msg "Construyendo $imagen (antes: $actual)"
docker build -q -t "$imagen" "$RAIZ/$pieza" >/dev/null
sed -i "s|image: blackjack-$pieza:[^ ]*|image: $imagen|" "$manifiesto"

# set image y no apply, para no deshacer un kubectl scale
replicas=$(k get deploy "$pieza" -o jsonpath='{.spec.replicas}')
k set image "deployment/$pieza" "$pieza=$imagen" >/dev/null
k rollout status "deployment/$pieza" --timeout=180s >/dev/null
# Espera a que no queden pods de la versión anterior
until [ "$(k get pods -l app="$pieza" -o jsonpath='{range .items[*]}{.spec.containers[0].image}{"\n"}{end}' | sort -u)" = "$imagen" ]; do sleep 1; done
esperar_cadena
msg "Publicado $imagen con $replicas réplica(s). Recuerda hacer commit de $(basename "$manifiesto") junto con el código."
