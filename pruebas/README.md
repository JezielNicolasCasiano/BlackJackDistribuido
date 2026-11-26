# Pruebas de punta a punta y experimentos (E1–E5)

Complementa `procesador/tests/` (que prueba el procesador solo, con un PostgreSQL desechable)
y reutiliza los scripts de `scripts/` (infraestructura). Aquí está lo que faltaba:

| Archivo | Para qué sirve | Sección del diseño |
|---|---|---|
| `tests/test_api.py` | Pruebas por la cadena real API → procesador → BD: idempotencia, versión, 409/404/422, concurrencia y reparto entre réplicas | 4.1, 6, 10 |
| `k6/blackjack.js` | Generador de carga: 4 estrategias, Idempotency-Key, versión, 3 reintentos con espera exponencial, `pedir` doble para E3 | 9, 10.3 |
| `verificar_integridad.py` | R5–R8 sobre la BD + comparación k6 vs BD (perdidas, duplicadas, aplicadas sin confirmar) | 7.2, 7.3 |
| `run_prueba.sh` | Una repetición completa de un experimento, usando `scripts/escalar.sh`, `reiniciar_datos.sh`, `eliminar_pod.sh`, `k6.sh`, etc. | 9.1, 9.4 |
| `resumir_resultados.py` | Tabla de medianas de las repeticiones y mejora % de E2 (no necesita el clúster) | 9.1, 9.4 |

## Correr los experimentos (en la compu que tiene Minikube)
```bash
scripts/preparar_entorno.sh                 # una vez después de arrancar el equipo
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt   # una vez

pruebas/run_prueba.sh e1 1 10 2m                          # E1 línea base
pruebas/run_prueba.sh e2 1 40 2m                          # E2: repetir con 2 y 3 réplicas
pruebas/run_prueba.sh e3 2 50 1m ninguno 0.3              # E3: repetir con 10, 25, 50, 100 y 200 VUs
pruebas/run_prueba.sh e4 3 40 3m procesador-forzada       # E4: también procesador-ordenada y con 1 réplica
pruebas/run_prueba.sh e5 2 40 3m procesador-reinicio      # E5
pruebas/run_prueba.sh e5 2 40 3m postgres-forzada         # E5 con la BD
IDEMPOTENCIA=off pruebas/run_prueba.sh control 2 40 3m procesador-reinicio   # control (9.3)
```
Cada escenario se repite 5 veces (se reporta la mediana). Los argumentos de `FALLO`
son los de `scripts/eliminar_pod.sh` (`procesador|postgres` + `ordenada|forzada|reinicio`).

## Resumir
```bash
python pruebas/resumir_resultados.py > pruebas/resultados/RESUMEN.md
```

## Pruebas de API (con el entorno levantado)
```bash
BASE_URL=http://$(minikube ip):30080 python -m pytest pruebas/tests/test_api.py -v -c /dev/null
REPLICAS_ESPERADAS=3 BASE_URL=... python -m pytest pruebas/tests/test_api.py -v -c /dev/null -k reparten
```
`-c /dev/null` evita que el `pytest.ini` de la raíz limite pytest a `procesador/tests`.

## Notas
- `k6.json.gz` va comprimido (sin comprimir serían cientos de MB) y no se sube a Git.
- La calibración previa (9.2) se hace con `run_prueba.sh e1 1 <VUs> 2m`, subiendo VUs hasta el máximo con 0 % de errores.
- k6 corre con `scripts/k6.sh` (Docker, dentro de la red de Minikube); `API_URL` ya viene definida.
