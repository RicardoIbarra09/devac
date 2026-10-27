#!/usr/bin/env python3
"""
Tabla resumen de E3 a partir de los meta.json de uno o varios directorios de corrida.

Uso:
  python resumen_e3.py ~/devac_resultados/e3/<JOB> [~/devac_resultados/e3/<JOB2> ...]
"""
import json
import math
import sys
from pathlib import Path

dirs = [Path(d) for d in (sys.argv[1:] or ["."])]
metas = [json.loads(p.read_text()) for d in dirs for p in d.glob("e3_*_meta.json")]
if not metas:
    sys.exit(f"No hay e3_*_meta.json en {', '.join(map(str, dirs))}")

by_variant = {}
for m in metas:
    by_variant.setdefault(m["variante"], []).append(m)

for variant in sorted(by_variant):
    ms = sorted(by_variant[variant], key=lambda m: m["n_eventos"])
    print(f"\nW4-{variant}  (tiempos: mediana entre repeticiones, en segundos)")
    hdr = (f"{'n':>6} {'aristas':>10} {'densidad':>9} {'pares %':>8} {'grafo':>8} {'louvain':>8} {'indices':>8} "
           f"{'total':>8} {'Q':>7} {'comun':>11} {'RAM MB':>8} {'sin ind.':>9} {'ident.':>6}")
    print(hdr)
    print("-" * len(hdr))
    for m in ms:
        r = m["resumen"]
        com = f"{r['comunidades_mediana']:g} ({r['comunidades_min']}-{r['comunidades_max']})"
        naive = "-" if r["sin_indice_t_pares_mediana_s"] is None else f"{r['sin_indice_t_pares_mediana_s']:.2f}"
        ident = "-" if r["sin_indice_grafo_identico"] is None else ("si" if r["sin_indice_grafo_identico"] else "NO")
        print(f"{m['n_eventos']:>6} {r['aristas']:>10,} {r['densidad']:>9.4f} {r['pares_comparados_pct']:>8.2f} "
              f"{r['t_grafo_mediana_s']:>8.3f} {r['t_louvain_mediana_s']:>8.3f} {r['t_indices_mediana_s']:>8.3f} "
              f"{r['t_total_mediana_s']:>8.3f} {r['q_mediana']:>7.3f} {com:>11} {r['ram_pico_construccion_mb']:>8.1f} "
              f"{naive:>9} {ident:>6}")
    if len(ms) >= 2:
        a, b = ms[0], ms[-1]
        k = math.log(b["resumen"]["t_total_mediana_s"] / a["resumen"]["t_total_mediana_s"]) / math.log(b["n_eventos"] / a["n_eventos"])
        print(f"Pendiente log-log del tiempo total (n={a['n_eventos']} -> {b['n_eventos']}): {k:.2f}  (2 = cuadratico)")

print("\n'pares %': pares comparados con el indice auxiliar respecto de n(n-1)/2."
      "\n'sin ind.': tiempo de comparar todos los pares; 'ident.': el grafo sin indice es identico al grafo con indice.")
