#!/usr/bin/env python3
# tabla de E7 a partir de los meta.json
# uso: python resumen_e7.py ~/devac_resultados/e7/<JOB>
import json
import sys
from pathlib import Path

dirs = [Path(d) for d in (sys.argv[1:] or ["."])]
metas = sorted((json.loads(p.read_text()) for d in dirs for p in d.glob("e7_*_meta.json")),
               key=lambda m: (m["operacion"], m["n_eventos"]))
if not metas:
    sys.exit(f"No hay e7_*_meta.json en {', '.join(map(str, dirs))}")


def f(v, w, d=1):
    return f"{v:>{w},.{d}f}" if isinstance(v, (int, float)) else f"{'-':>{w}}"


for m in metas:
    print(f"\nn={m['n_eventos']}, cambios={m['operacion']} a {m['ritmo_cambios_por_s']:g}/s, ventana {m['ventana_s']:g} s, "
          f"calentamiento {m['calentamiento_s']:g} s, {m['repeticiones']} rep., {m['nucleos_fisicos']} nucleos fisicos / "
          f"{m.get('cpus_logicos', '?')} logicos "
          f"(mediana; lecturas en us)")
    h = (f"{'K':>3} {'ubicacion':>14} {'lect/s reposo':>13} {'acel.':>6} {'lect/s cambios':>14} {'p95 reposo':>10} {'p95 cambios':>11} "
         f"{'razon':>6} {'p95 reac.':>9} {'aplicar ms':>10} {'reac. replica ms':>16} {'propag p95 ms':>13} "
         f"{'ventana max ms':>14} {'reac. prim s':>12} {'cambios':>7} {'mem MB':>7} {'inconsist.':>10} {'err':>4} {'final ok':>8}")
    print(h)
    print("-" * len(h))
    base = None
    for k, r in sorted(m["resumen"].items(), key=lambda kv: int(kv[0])):
        base = base or r["lect_por_s_reposo"]
        acc = r["lect_por_s_reposo"] / base if base else None
        print(f"{int(k):>3} {r.get('ubicacion', '-'):>14} {f(r['lect_por_s_reposo'], 13, 0)} {f(acc, 6, 2)} {f(r['lect_por_s_cambios'], 14, 0)} "
              f"{f(r['lectura_reposo_p95_us'], 10)} {f(r['lectura_cambios_p95_us'], 11)} {f(r['razon_p95'], 6, 2)} "
              f"{f(r['lectura_reacomodo_primario_p95_us'], 9)} {f(r['aplicar_cambio_p95_ms'], 10, 2)} "
              f"{f(r['aplicar_reacomodo_ms'], 16, 0)} {f(r['propagacion_p95_ms'], 13, 2)} {f(r['ventana_todas_max_ms'], 14, 1)} "
              f"{f(r['reacomodo_primario_s'], 12, 2)} {f(r['cambios_aplicados'], 7, 0)} {f(r['memoria_replica_mb'], 7, 0)} "
              f"{r['inconsistentes']:>10} {r['errores_lector'] + r['errores_escritor']:>4} {r['final_ok_min_pct']:>7.1f}%")

print("\n'ubicacion': fisicos = cada proceso en su nucleo; hyperthreading = dos procesos por nucleo; saturado =")
print("mas procesos que CPUs, el sistema los turna.")
print("K = procesos lectores (replicas). 'acel.' = lecturas por segundo respecto a K=1. 'razon' = p95 con cambios /")
print("p95 en reposo. 'p95 reac.' = lectura p95 mientras el primario reacomoda. 'aplicar ms' = lo que tarda una")
print("replica en aplicar un cambio (p95); 'reac. replica ms' = lo que tarda en aplicar la particion nueva.")
print("'propag p95' = desde que el primario publica un cambio hasta que una replica lo aplica; 'ventana max' = lo")
print("mas que tardo un cambio en llegar a todas. Criterio: 0 inconsistentes, 0 errores, final ok 100%.")
