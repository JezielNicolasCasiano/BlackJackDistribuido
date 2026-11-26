#!/usr/bin/env bash
# Uso: verificar.sh DIRECTORIO
# Copia las tablas a DIRECTORIO/bd/ (son parte de los resultados originales) y
# corre verificar.py, que revisa R1-R8 y compara con el registro de k6 si existe.
set -euo pipefail
source "$(dirname "$0")/comun.sh"
dir=${1:?Uso: verificar.sh DIRECTORIO}
mkdir -p "$dir/bd"

esperar_bd
for tabla in partidas cartas solicitudes; do
  psql_bd -c "COPY $tabla TO STDOUT WITH (FORMAT csv, HEADER)" | gzip > "$dir/bd/$tabla.csv.gz"
done
msg "Tablas copiadas en $dir/bd/"
python3 "$RAIZ/scripts/verificar.py" "$dir"
