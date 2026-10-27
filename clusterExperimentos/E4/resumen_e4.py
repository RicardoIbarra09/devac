#!/usr/bin/env python3
"""
Junta las repeticiones de E4 (una tarea de Slurm = una repeticion) en el <escenario>_<etiqueta>_meta.json
que lee graficas/make_charts.py, y muestra una tabla resumen.

Uso:
  python3 resumen_e4.py ~/devac_agregados/e4/<JOB>/politicas_un_evento [otra carpeta ...]
  poetry run python graficas/make_charts.py --data-dir <carpeta> --out-dir <figuras>
"""
import json
import re
import statistics
import sys
from pathlib import Path

PART = re.compile(r"^(?P<config>.+)_r(?P<rep>\d+)_meta\.json$")
# deben coincidir entre repeticiones para poder juntarlas
SAME = ("script", "plan_counts", "commit", "dirty")
SAME_PARAMS = ("policies", "scenario", "changes", "stress_threshold", "similarity_threshold", "no_audit", "seed", "tag")


def merge(folder: Path) -> list:
    groups = {}
    for path in sorted(folder.glob("*_r*_meta.json")):
        match = PART.match(path.name)
        if match:
            groups.setdefault(match["config"], []).append((int(match["rep"]), json.loads(path.read_text(encoding="utf-8"))))

    merged = []
    for config, parts in sorted(groups.items()):
        parts.sort(key=lambda item: item[0])
        first = parts[0][1]
        for rep, meta in parts[1:]:
            for key in SAME:
                if meta[key] != first[key]:
                    sys.exit(f"{folder}/{config}: la repeticion {rep} tiene otro {key} ({meta[key]} vs {first[key]})")
            for key in SAME_PARAMS:
                if meta["params"][key] != first["params"][key]:
                    sys.exit(f"{folder}/{config}: la repeticion {rep} tiene otro parametro {key}")
        reps = [rep for rep, _ in parts]
        combined = {
            **{k: v for k, v in first.items() if k not in ("files", "replications", "argv", "finished_utc")},
            "params": {**first["params"], "rep": None, "reps": len(reps)},
            "reps": reps,
            "started_utc": min(m["started_utc"] for _, m in parts),
            "finished_utc": max(m["finished_utc"] for _, m in parts),
            "nodes": sorted({m.get("node") for _, m in parts}),
            "cpus": sorted({m.get("cpu") for _, m in parts}),
            "files": [f for _, m in parts for f in m["files"]],
            "replications": [r for _, m in parts for r in m["replications"]],
        }
        missing = [f for f in combined["files"] if not (folder / f).exists()]
        if missing:
            sys.exit(f"{folder}/{config}: faltan {missing}")
        (folder / f"{config}_meta.json").write_text(json.dumps(combined, indent=2, ensure_ascii=False), encoding="utf-8")
        merged.append((config, combined))
    return merged


def _mean(rows, key, scale=1.0):
    values = [r[key] for r in rows if r.get(key) is not None]
    return statistics.fmean(values) * scale if values else float("nan")


def main() -> None:
    folders = [Path(d) for d in (sys.argv[1:] or ["."])]
    for folder in folders:
        merged = merge(folder)
        if not merged:
            sys.exit(f"No hay *_rNN_meta.json en {folder}")
        print(f"\n{folder}  (commit {merged[0][1]['commit']}, promedio entre repeticiones)")
        hdr = (f"{'configuracion':<24} {'reps':>4} {'recons':>7} {'estres max':>10} {'Q completa ms':>13} "
               f"{'Q incr us':>10} {'grafo us':>9} {'veces':>7} {'|dQ| max':>9} {'recons s':>9} {'2a pasada s':>11}")
        print(hdr)
        print("-" * len(hdr))
        for config, meta in merged:
            rows = meta["replications"]
            full, inc = _mean(rows, "mean_t_networkx_s", 1e3), _mean(rows, "mean_t_incremental_s", 1e6)
            print(f"{config:<24} {len(rows):>4} {_mean(rows, 'rebuilds'):>7.1f} {_mean(rows, 'max_stress_q'):>10.3f} "
                  f"{full:>13.1f} {inc:>10.0f} {_mean(rows, 'mean_t_graph_s', 1e6):>9.0f} {full * 1e3 / inc:>7.0f} "
                  f"{max(r['max_abs_q_diff'] for r in rows):>9.1e} {_mean(rows, 'mean_rebuild_s'):>9.1f} "
                  f"{_mean(rows, 'mean_second_pass_s'):>11.2f}")
    print("\n'veces': Q completa / Q incremental. 'recons s': tiempo medio de una reconstruccion por umbral."
          "\n'2a pasada s': grafo sin excluidos + segunda corrida de louvain, por reconstruccion (incluye el build inicial).")


if __name__ == "__main__":
    main()
