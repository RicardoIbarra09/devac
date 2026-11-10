#!/usr/bin/env python3
# tabla de E5 a partir de los meta.json de uno o varios directorios
# uso: python resumen_e5.py ~/devac_resultados/e5/<JOB> [~/devac_resultados/e5/<JOB2> ...]
import json
import sys
from pathlib import Path

dirs = [Path(d) for d in (sys.argv[1:] or ["."])]
metas = sorted((json.loads(p.read_text()) for d in dirs for p in d.glob("e5_*_meta.json")),
               key=lambda m: (m["carga"], m["gil_switch_interval_s"], m["n_eventos"]))
for m in metas:  # una tabla por carga y turno del GIL
    m["grupo"] = f"{m['carga']}, turno del GIL {m['gil_switch_interval_s'] * 1000:g} ms"
if not metas:
    sys.exit(f"No hay e5_*_meta.json en {', '.join(map(str, dirs))}")

hdr = (f"{'n':>6} {'op':>7} {'op p50 ms':>10} {'op p95 ms':>10} {'evaluador':>10} {'monitor':>8} "
       f"{'lect. reposo':>13} {'lect. cambios':>14} {'razon':>6} {'traslapadas':>12} {'inconsist.':>10} "
       f"{'err lect':>8} {'err escr':>8} {'final ok':>8}")
for carga in sorted({m["grupo"] for m in metas}):
    print(f"\n{carga}  (mediana entre repeticiones; lecturas = p95 en microsegundos)")
    print(hdr)
    print("-" * len(hdr))
    for m in (m for m in metas if m["grupo"] == carga):
        for op, r in m["resumen"].items():
            print(f"{m['n_eventos']:>6} {op:>7} {r['op_p50_ms']:>10.2f} {r['op_p95_ms']:>10.2f} "
                  f"{r['evaluador_p50_ms']:>10.2f} {r['monitor_p50_ms']:>8.2f} {r['lectura_reposo_p95_us']:>13.1f} "
                  f"{r['lectura_cambios_p95_us']:>14.1f} {r['razon_p95']:>6.2f} {r['lecturas_traslapadas']:>12,} "
                  f"{r['inconsistentes']:>10} {r['errores_lector']:>8} {r['errores_escritor']:>8} "
                  f"{r['final_ok_min_pct']:>7.1f}%")
    for m in (m for m in metas if m["grupo"] == carga):
        bad = [d for d in m["detalle"] if d["ejemplos_inconsistentes"] or d["errores_lector"] or d["errores_escritor"]]
        for d in bad[:5]:
            print(f"  n={m['n_eventos']} rep {d['rep']} {d['op']}: errores lector={d['errores_lector']} "
                  f"escritor={d['errores_escritor']} ejemplos={d['ejemplos_inconsistentes'][:2]}")

print("\nCriterio del plan: 0 inconsistentes, 0 errores y razon p95 (cambios / reposo) de 2 o menos.")
print("'traslapadas' son lecturas que ocurrieron mientras un cambio estaba a medias; 'final ok' compara el")
print("estado final de DEvAC con el oraculo.")
