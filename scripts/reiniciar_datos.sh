#!/usr/bin/env bash
# Vacía las tablas antes de cada repetición. TRUNCATE porque borrar el namespace
# no siempre borra los datos en Minikube.
set -euo pipefail
source "$(dirname "$0")/comun.sh"

esperar_bd
psql_bd <<'SQL'
DO $$
DECLARE tablas text;
BEGIN
  SELECT string_agg(format('%I.%I', schemaname, tablename), ', ')
    INTO tablas FROM pg_tables WHERE schemaname = 'public';
  IF tablas IS NOT NULL THEN
    EXECUTE 'TRUNCATE ' || tablas || ' RESTART IDENTITY CASCADE';
  END IF;
END $$;
SQL
filas=$(psql_bd <<'SQL'
SELECT coalesce(sum((xpath('/row/c/text()',
       query_to_xml(format('SELECT count(*) AS c FROM %I.%I', schemaname, tablename), false, true, '')))[1]::text::int), 0)
FROM pg_tables WHERE schemaname = 'public';
SQL
)
[ "$filas" -eq 0 ] || { msg "ERROR: quedaron $filas filas"; exit 1; }
esperar_pods_listos
esperar_cadena
msg "Datos reiniciados: 0 filas en $(psql_bd -c "SELECT count(*) FROM pg_tables WHERE schemaname='public'") tabla(s); cadena respondiendo"
