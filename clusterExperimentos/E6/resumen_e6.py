#!/usr/bin/env python3
# tablas de E6 a partir de los meta.json
# uso: python resumen_e6.py ~/devac_resultados/e6/<JOB>
import json
import sys
from pathlib import Path

dirs = [Path(d) for d in (sys.argv[1:] or ["."])]
metas = sorted((json.loads(p.read_text()) for d in dirs for p in d.glob("e6_*_meta.json")),
               key=lambda m: m["n_eventos"])
if not metas:
    sys.exit(f"No hay e6_*_meta.json en {', '.join(map(str, dirs))}")


def f(v, w, d=1):
    return f"{v:>{w},.{d}f}" if isinstance(v, (int, float)) else f"{'-':>{w}}"


print("\nA. Recuperacion: 1,000 modificaciones sin reacomodo, luego un reacomodo (mediana; lecturas p95 en us)")
h = (f"{'n':>6} {'Q ini':>6} {'Q deg':>6} {'Q rec':>6} {'lect ini':>9} {'lect deg':>9} {'lect rec':>9} "
     f"{'degrad':>7} {'rec/ini':>7} {'reacomodo s':>11} {'grafo':>6} {'louvain':>7} {'indices':>7} {'Q':>5} "
     f"{'preserv %':>9} {'oraculo %':>9}")
print(h)
print("-" * len(h))
for m in metas:
    a = m["resumen_recuperacion"]
    print(f"{m['n_eventos']:>6} {f(a['q_inicial'], 6, 3)} {f(a['q_degradada'], 6, 3)} {f(a['q_recuperada'], 6, 3)} "
          f"{f(a['lectura_inicial_p95_us'], 9)} {f(a['lectura_degradada_p95_us'], 9)} {f(a['lectura_recuperada_p95_us'], 9)} "
          f"{f(a['degradacion'], 7, 2)} {f(a['recuperada_vs_inicial'], 7, 2)} {f(a['reacomodo_s'], 11, 2)} "
          f"{f(a['t_grafo_s'], 6, 2)} {f(a['t_louvain_s'], 7, 2)} {f(a['t_indices_s'], 7, 2)} {f(a['t_q_s'], 5, 2)} "
          f"{f(a['decisiones_preservadas_pct'], 9, 1)} {f(a['oraculo_despues_pct'], 9, 1)}")

print("\nB. Concurrente: el escritor aplica las mismas modificaciones con el reacomodo encendido (umbral tau)")
h = (f"{'n':>6} {'tau':>5} {'reacom.':>7} {'dura p50 s':>10} {'escritor s':>10} {'lect reposo':>11} "
     f"{'lect final':>10} {'final/ini':>9} {'lect cambios':>12} {'lect reacom':>11} {'razon reac':>10} "
     f"{'Q final':>7} {'lect en reac':>12} {'inconsist.':>10} {'err':>4} {'final ok':>8}")
print(h)
print("-" * len(h))
for m in metas:
    for tau, b in m["resumen_concurrente"].items():
        print(f"{m['n_eventos']:>6} {tau:>5} {f(b['reacomodos'], 7, 0)} {f(b['reacomodo_p50_s'], 10, 2)} "
              f"{f(b['escritor_total_s'], 10, 1)} {f(b['lectura_reposo_p95_us'], 11)} {f(b['lectura_reposo_final_p95_us'], 10)} "
              f"{f(b['deriva_final'], 9, 2)} {f(b['lectura_cambios_p95_us'], 12)} {f(b['lectura_reacomodo_p95_us'], 11)} "
              f"{f(b['razon_reacomodo_p95'], 10, 2)} {f(b['q_final'], 7, 3)} {b['lecturas_en_reacomodo']:>12,} "
              f"{b['inconsistentes']:>10} {b['errores_lector'] + b['errores_escritor']:>4} {b['final_ok_min_pct']:>7.1f}%")

print("\nA: 'degrad' = lectura degradada / inicial; 'rec/ini' = recuperada / inicial (1.0 = recupero todo);")
print("'preserv %' = decisiones iguales antes y despues del reacomodo; 'oraculo %' = coinciden con el oraculo.")
print("B: 'final/ini' = lector solo al final / al inicio; 'razon reac' = lectura durante el reacomodo / reposo;")
print("tau=inf es el reacomodo apagado (como E5). Criterio: 0 inconsistentes, 0 errores, final ok 100%.")
