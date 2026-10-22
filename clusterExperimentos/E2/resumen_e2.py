#!/usr/bin/env python3
"""
Tabla resumen de E2 a partir de los meta.json de una corrida.

Uso:
  python resumen_e2.py ~/devac_resultados/e2/<JOB>
"""
import json
import sys
from pathlib import Path

d = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
metas = sorted((json.loads(p.read_text()) for p in d.glob("e2_n*_meta.json")), key=lambda m: m["n_eventos"])
if not metas:
    sys.exit(f"No hay e2_n*_meta.json en {d}")

hdr = f"{'n':>6} {'comun':>6} {'p50 DEvAC':>10} {'p50 lin':>10} {'p99 DEvAC':>10} {'p99 lin':>10} {'speedup':>8} {'conc_lin':>8} {'conc_orc':>8} {'FP':>4} {'mem MB':>7}"
print(hdr)
print("-" * len(hdr))
for m in metas:
    r = m["resumen"]
    orc = r["concordancia_devac_oraculo_pct"]
    print(f"{m['n_eventos']:>6} {m['n_comunidades']:>6} {r['p50_devac_us']:>10.1f} {r['p50_lineal_us']:>10.1f} "
          f"{r['p99_devac_us']:>10.1f} {r['p99_lineal_us']:>10.1f} {r['speedup_mean_mediana']:>8.2f} "
          f"{r['concordancia_lineal_pct']:>8.2f} {('-' if orc is None else f'{orc:.2f}'):>8} "
          f"{('-' if r['fp_devac'] is None else r['fp_devac']):>4} {r['memoria_indices_devac_mb']:>7.1f}")
print("\nLatencias en microsegundos: mediana entre repeticiones del p50/p99 de cada repeticion.")
