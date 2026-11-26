#!/usr/bin/env python3
"""Resume pruebas/resultados/ en una tabla de MEDIANAS por prueba y configuración (sección 9.1).

No necesita el clúster: solo lee las carpetas que dejó pruebas/run_prueba.sh
(resumen_k6.json, verificacion.json, recursos.csv, fallos.csv, k6.json.gz).
Uso (desde la raíz del repo):
    python pruebas/resumir_resultados.py > pruebas/resultados/RESUMEN.md
"""
import csv
import gzip
import json
import re
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

NOMBRE = re.compile(r"^\d{8}-\d{6}-r(\d+)-v(\d+)-(.+)$")


def med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else None


def fmt(x, d=1):
    return "-" if x is None else f"{x:.{d}f}"


def metrica(resumen, nombre, campo):
    return resumen.get("metrics", {}).get(nombre, {}).get(campo)


def tasa_error(resumen):
    m = resumen.get("metrics", {}).get("http_req_failed", {})
    if "passes" in m and "fails" in m and (m["passes"] + m["fails"]) > 0:
        return 100.0 * m["passes"] / (m["passes"] + m["fails"])
    if "value" in m:
        return 100.0 * m["value"]
    return None


def cpu_por_pod(archivo):
    """CPU promedio (milicores) de los pods del procesador. Formato de scripts/capturar_recursos.sh."""
    if not archivo.exists():
        return None
    por_pod = defaultdict(list)
    with archivo.open(newline="") as f:
        for fila in csv.DictReader(f):
            if fila["pod"].startswith("procesador"):
                por_pod[fila["pod"]].append(float(fila["cpu_milicores"]))
    if not por_pod:
        return None
    return sum(sum(v) / len(v) for v in por_pod.values()) / len(por_pod)


def ultimo_fallo(archivo):
    """Última fila de fallos.csv (formato de scripts/eliminar_pod.sh) o None."""
    if not archivo.exists():
        return None
    with archivo.open(newline="") as f:
        filas = list(csv.DictReader(f))
    return filas[-1] if filas else None


def t_pod_listo(dir_):
    fila = ultimo_fallo(dir_ / "fallos.csv")
    try:
        return float(fila["segundos_hasta_listo"]) if fila else None
    except (KeyError, ValueError):
        return None


def t_sin_errores(dir_):
    """Segundos desde el fallo hasta el primer segundo sin errores (tras haber visto errores)."""
    fila = ultimo_fallo(dir_ / "fallos.csv")
    kj = dir_ / "k6.json.gz"
    if not fila or not kj.exists():
        return None
    t0 = datetime.fromisoformat(fila["marca_fallo"]).timestamp()
    reqs, fallos = defaultdict(int), defaultdict(int)
    with gzip.open(kj, "rt", encoding="utf-8") as f:
        for linea in f:
            if '"http_req_failed"' not in linea or '"Point"' not in linea:
                continue
            d = json.loads(linea)
            if d.get("metric") != "http_req_failed":
                continue
            seg = int(datetime.fromisoformat(d["data"]["time"]).timestamp())
            if seg >= int(t0):
                reqs[seg] += 1
                fallos[seg] += int(d["data"]["value"])
    hubo_error = False
    for seg in sorted(reqs):
        if fallos[seg] > 0:
            hubo_error = True
        elif hubo_error:
            return seg - t0
    return 0.0 if not hubo_error else None


def main():
    raiz = Path(sys.argv[1] if len(sys.argv) > 1 else "pruebas/resultados")
    grupos = defaultdict(list)
    for d in sorted(raiz.glob("*/*")):
        m = NOMBRE.match(d.name)
        if m and d.is_dir():
            grupos[(d.parent.name, int(m[1]), int(m[2]), m[3])].append(d)

    print("# Resumen de resultados (mediana de las repeticiones)\n")
    print("| Prueba | Réplicas | VUs | Fallo | n | Req/s | p50 ms | p95 ms | % error | 409 | "
          "CPU/pod (m) | Violaciones | Perdidas | Duplicadas | Sin confirmar | Pod listo (s) | 1er seg sin errores (s) |")
    print("|" + "---|" * 17)
    base_rps, filas = {}, []
    for (prueba, rep, vus, fallo), dirs in sorted(grupos.items()):
        c = defaultdict(list)
        for d in dirs:
            rs = json.loads((d / "resumen_k6.json").read_text()) if (d / "resumen_k6.json").exists() else {}
            c["rps"].append(metrica(rs, "http_reqs", "rate"))
            c["p50"].append(metrica(rs, "http_req_duration", "med"))
            c["p95"].append(metrica(rs, "http_req_duration", "p(95)"))
            c["err"].append(tasa_error(rs))
            c["c409"].append(metrica(rs, "conflictos_409", "count"))
            c["cpu"].append(cpu_por_pod(d / "recursos.csv"))
            if (d / "verificacion.json").exists():
                v = json.loads((d / "verificacion.json").read_text())
                k = v.get("comparacion_k6", {})
                c["viol"].append(v.get("violaciones_total"))
                c["perd"].append(k.get("perdidas"))
                c["dup"].append(k.get("duplicadas"))
                c["sinc"].append(k.get("aplicadas_sin_confirmar"))
            c["listo"].append(t_pod_listo(d))
            c["sinerr"].append(t_sin_errores(d))
        if prueba == "e2" and rep == 1 and med(c["rps"]):
            base_rps[(vus, fallo)] = med(c["rps"])
        filas.append((prueba, rep, vus, fallo, len(dirs), c))
        print(f"| {prueba} | {rep} | {vus} | {fallo} | {len(dirs)} | {fmt(med(c['rps']))} | {fmt(med(c['p50']))} | "
              f"{fmt(med(c['p95']))} | {fmt(med(c['err']), 2)} | {fmt(med(c['c409']), 0)} | {fmt(med(c['cpu']), 0)} | "
              f"{fmt(med(c['viol']), 0)} | {fmt(med(c['perd']), 0)} | {fmt(med(c['dup']), 0)} | {fmt(med(c['sinc']), 0)} | "
              f"{fmt(med(c['listo']), 1)} | {fmt(med(c['sinerr']), 1)} |")

    e2 = [(rep, vus, fallo, med(c["rps"])) for p, rep, vus, fallo, _, c in filas if p == "e2"]
    if e2 and base_rps:
        print("\n## E2: mejora % = (R - R1) / R1 x 100\n")
        print("| Réplicas | Req/s | Mejora % |\n|---|---|---|")
        for rep, vus, fallo, r in e2:
            r1 = base_rps.get((vus, fallo))
            print(f"| {rep} | {fmt(r)} | {fmt(100 * (r - r1) / r1) if r1 and r else '-'} |")
    print("\n_Criterios (sección 9.2): E1 variación entre repeticiones < 10 %; E3 y E4 con 0 violaciones; "
          "E5 con 0 perdidas, 0 duplicadas y 0 violaciones. Los 409 no cuentan como error (9.4)._")


if __name__ == "__main__":
    main()
