#!/usr/bin/env bash
# Uso: publicar_version.sh api|procesador VERSION
# Construye, actualiza el manifiesto y despliega. Cada cambio lleva versión nueva.
set -euo pipefail
source "$(dirname "$0")/comun.sh"
pieza=${1:?Uso: publicar_version.sh api|procesador VERSION}; version=${2:?Falta la VERSION, por ejemplo 0.3}

manifiesto=$(manifiesto_de "$pieza") || { msg "Pieza inválida: $pieza (api o procesador)"; exit 1; }
[[ "$version" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || { msg "Versión inválida: $version (ejemplo 0.3)"; exit 1; }

imagen="blackjack-$pieza:$version"
actual=$(imagen_de "$manifiesto")
eval "$(minikube docker-env)"
if docker image inspect "$imagen" >/dev/null 2>&1; then
  msg "ERROR: $imagen ya existe. Usa una versión nueva (la actual es $actual)."; exit 1
fi

msg "Construyendo $imagen (antes: $actual)"
docker build -q -t "$imagen" "$RAIZ/$pieza" >/dev/null
sed -i "s|image: blackjack-$pieza:[^ ]*|image: $imagen|" "$manifiesto"

# set image y no apply, para no deshacer un kubectl scale
k set image "deployment/$pieza" "$pieza=$imagen" >/dev/null
esperar_rollout "$pieza"   # incluye esperar a que no queden pods de la versión anterior
esperar_cadena
msg "Publicado $imagen con $(replicas "$pieza") réplica(s). Recuerda hacer commit de $(basename "$manifiesto") junto con el código."
