"""Uso: python3 scripts/resumir_cpu.py DIRECTORIO

Con las lecturas de cpu_pods.sh al inicio y al final de la medición
(DIRECTORIO/cpu_contadores.csv), calcula para cada pod la CPU promedio en
milicores y el porcentaje de periodos en que su límite lo frenó. Escribe
DIRECTORIO/cpu.csv. Un pod que no estuvo en las dos lecturas (reemplazado por
un fallo) no aparece. Si falta la lectura final (corrida abortada o interrumpida)
avisa y termina sin error, sin escribir cpu.csv.
"""
import csv
import sys
from pathlib import Path

directorio = Path(sys.argv[1])
archivo = directorio / "cpu_contadores.csv"
lecturas = {}
if archivo.exists():
    with open(archivo, newline="") as f:
        for fila in csv.DictReader(f):
            lecturas[(fila["etiqueta"], fila["pod"])] = fila

filas = []
for (etiqueta, pod), ini in sorted(lecturas.items()):
    fin = lecturas.get(("fin", pod))
    if etiqueta != "inicio" or fin is None:
        continue
    segundos = float(fin["marca_s"]) - float(ini["marca_s"])
    d = {k: int(fin[k]) - int(ini[k]) for k in ("usage_usec", "nr_periods", "nr_throttled")}
    milicores = d["usage_usec"] / segundos / 1000
    # PostgreSQL no tiene límite: no hay periodos que contar
    frenado = 100 * d["nr_throttled"] / d["nr_periods"] if d["nr_periods"] else 0.0
    filas.append([pod, f"{segundos:.1f}", f"{milicores:.0f}", f"{frenado:.1f}"])

if not filas:
    # Sin pares inicio/fin no hay intervalo que medir: la corrida terminó antes de
    # la lectura final (aborto por calor o Ctrl+C) o antes de la inicial
    print("CPU durante la medición: sin datos (falta la lectura inicial o la final; "
          "¿corrida abortada o interrumpida?)")
    sys.exit(0)

with open(directorio / "cpu.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["pod", "segundos", "cpu_milicores", "periodos_frenados_pct"])
    w.writerows(filas)
print("CPU durante la medición (milicores; % de periodos frenados por el límite):")
for pod, _, mc, fr in filas:
    print(f"  {pod:<32} {mc:>5} m  {fr:>5} %")
