#!/usr/bin/env python3
"""
Tabla resumen de E2 a partir de los meta.json de uno o varios directorios de corrida.
Imprime una tabla por variante de W4 (U = uniforme, E<p_mix> = estructurada).

Uso:
  python resumen_e2.py ~/devac_resultados/e2/<JOB>
  python resumen_e2.py ~/devac_resultados/e2/<JOB_U> ~/devac_resultados/e2/<JOB_E>
"""
import json
import math
import sys
from pathlib import Path

dirs = [Path(d) for d in (sys.argv[1:] or ["."])]
metas = [json.loads(p.read_text()) for d in dirs for p in d.glob("e2_*meta.json")]
if not metas:
    sys.exit(f"No hay e2_*meta.json en {', '.join(map(str, dirs))}")

by_variant = {}
for m in metas:
    by_variant.setdefault(m.get("variante", "U"), []).append(m)

for variant in sorted(by_variant):
    ms = sorted(by_variant[variant], key=lambda m: m["n_eventos"])
    print(f"\nW4-{variant}")
    hdr = (f"{'n':>6} {'comun':>6} {'mayor':>6} {'p50 DEvAC':>10} {'p50 lin':>10} {'p99 DEvAC':>10} "
           f"{'p99 lin':>10} {'speedup':>8} {'conc_lin':>8} {'conc_orc':>8} {'FP':>4} {'mem MB':>7}")
    print(hdr)
    print("-" * len(hdr))
    for m in ms:
        r = m["resumen"]
        orc = r["concordancia_devac_oraculo_pct"]
        print(f"{m['n_eventos']:>6} {m['n_comunidades']:>6} {m.get('comunidad_mayor', '-'):>6} "
              f"{r['p50_devac_us']:>10.1f} {r['p50_lineal_us']:>10.1f} "
              f"{r['p99_devac_us']:>10.1f} {r['p99_lineal_us']:>10.1f} {r['speedup_mean_mediana']:>8.2f} "
              f"{r['concordancia_lineal_pct']:>8.2f} {('-' if orc is None else f'{orc:.2f}'):>8} "
              f"{('-' if r['fp_devac'] is None else r['fp_devac']):>4} {r['memoria_indices_devac_mb']:>7.1f}")
    # Pendiente log-log del p50 de DEvAC entre el primer y el ultimo tamano
    if len(ms) >= 2:
        a, b = ms[0], ms[-1]
        k_dev = math.log(b["resumen"]["p50_devac_us"] / a["resumen"]["p50_devac_us"]) / math.log(b["n_eventos"] / a["n_eventos"])
        k_lin = math.log(b["resumen"]["p50_lineal_us"] / a["resumen"]["p50_lineal_us"]) / math.log(b["n_eventos"] / a["n_eventos"])
        print(f"Pendiente log-log del p50 (n={a['n_eventos']} -> {b['n_eventos']}): DEvAC {k_dev:.2f}, lineal {k_lin:.2f}"
              "  (1 = crecimiento lineal; menor que 1 = sublineal)")

print("\nLatencias en microsegundos: mediana entre repeticiones del p50/p99 de cada repeticion.")
