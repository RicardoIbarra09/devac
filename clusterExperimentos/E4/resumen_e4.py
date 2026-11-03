#!/usr/bin/env python3
# tabla de E4 por carga a partir de los meta.json de uno o varios directorios
# uso: python resumen_e4.py ~/devac_resultados/e4/<JOB> [~/devac_resultados/e4/<JOB2> ...]
import json
import sys
from pathlib import Path

dirs = [Path(d) for d in (sys.argv[1:] or ["."])]
metas = [json.loads(p.read_text()) for d in dirs for p in d.glob("e4_*_meta.json")]
if not metas:
    sys.exit(f"No hay e4_*_meta.json en {', '.join(map(str, dirs))}")

by_load = {}
for m in metas:
    by_load.setdefault((m["carga"], m["n_eventos"]), []).append(m)

for (carga, n), ms in sorted(by_load.items()):
    ms.sort(key=lambda m: m["theta"])
    print(f"\n{carga}, {n:,} eventos  (mediana entre repeticiones; latencias en microsegundos)")
    hdr = (f"{'θ':>5} {'conexiones':>11} {'aislados':>8} {'comunidades':>13} {'mayor':>6} {'Q':>6} {'constr s':>9} "
           f"{'p50 DEvAC':>10} {'p95 DEvAC':>10} {'p95 lin':>9} {'speedup':>8} {'pasos':>7} {'conc lin':>8} {'conc orc':>8} {'FP':>4}")
    print(hdr)
    print("-" * len(hdr))
    for m in ms:
        r = m["resumen"]
        com = f"{r['comunidades_mediana']:g} ({r['comunidades_min']}-{r['comunidades_max']})"
        orc = "-" if r["concordancia_oraculo_min_pct"] is None else f"{r['concordancia_oraculo_min_pct']:.2f}"
        pasos = "-" if r["pasos_media_mediana"] is None else f"{r['pasos_media_mediana']:.0f}"
        fp = "-" if r["fp_total"] is None else r["fp_total"]
        print(f"{m['theta']:>5g} {r['conexiones']:>11,} {r['aislados']:>8,} {com:>13} {r['comunidad_mayor_mediana']:>6g} "
              f"{r['q_mediana']:>6.3f} {r['construccion_mediana_s']:>9.2f} {r['devac_p50_us']:>10.1f} {r['devac_p95_us']:>10.1f} "
              f"{r['lineal_p95_us']:>9.1f} {r['speedup_mean_mediana']:>8.2f} {pasos:>7} "
              f"{r['concordancia_lineal_min_pct']:>8.2f} {orc:>8} {fp:>4}")
    # criterio del plan: el θ con menor p95 entre los que tienen 100% de concordancia
    ok = [m for m in ms if m["resumen"]["concordancia_lineal_min_pct"] == 100.0
          and m["resumen"]["concordancia_oraculo_min_pct"] in (100.0, None)]
    if ok:
        best = min(ok, key=lambda m: m["resumen"]["devac_p95_us"])
        print(f"θ elegido (menor p95 con 100% de concordancia): {best['theta']:g}")
    else:
        print("Ningun θ tuvo 100% de concordancia en todas las repeticiones: revisar antes de elegir")

print("\n'conc lin' y 'conc orc' son el minimo entre repeticiones; 'FP' es la suma de todas las repeticiones.")
