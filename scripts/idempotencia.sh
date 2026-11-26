#!/usr/bin/env bash
# Uso: idempotencia.sh on|off
# Enciende o apaga la clave de idempotencia del procesador (experimento de control).
# "off" sobrescribe IDEMPOTENCIA en el Deployment con set env (y no con apply, para
# no deshacer un kubectl scale). "on" quita esa sobrescritura, de modo que manda el
# valor del ConfigMap ("on"); así el modo queda bien sin depender de lo que haga apply.
set -euo pipefail
source "$(dirname "$0")/comun.sh"
modo=${1:?Uso: idempotencia.sh on|off}
[[ "$modo" =~ ^(on|off)$ ]] || { msg "Modo inválido: $modo (on u off)"; exit 1; }

if [ "$modo" = on ]; then
  k set env deployment/procesador IDEMPOTENCIA- >/dev/null
else
  k set env deployment/procesador IDEMPOTENCIA=off >/dev/null
fi
# Incluye esperar a que no queden pods con el modo anterior (preStop los deja vivos 5 s)
esperar_rollout procesador
for p in $(k get pods -l app=procesador -o name); do
  actual=$(k exec "$p" -- python -c "import urllib.request,json; print(json.load(urllib.request.urlopen('http://localhost:8000/quien'))['idempotencia'])")
  [ "$actual" = "$modo" ] || { msg "ERROR: $p sigue en $actual"; exit 1; }
done
esperar_cadena
msg "Idempotencia en $modo en todas las réplicas"
