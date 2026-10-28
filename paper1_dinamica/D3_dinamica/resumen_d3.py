#!/usr/bin/env python3
# junta las repeticiones de D3 (una tarea de slurm = una repeticion) en resultados/d3_dinamica.csv,
# una fila por conjunto, configuracion y repeticion, y muestra una tabla resumen
#
# uso:
#   python3 paper1_dinamica/D3_dinamica/resumen_d3.py                  (job mas reciente de resultados/crudo/d3)
#   python3 paper1_dinamica/D3_dinamica/resumen_d3.py resultados/crudo/d3/<JOB>

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datos import RESULTS, job_dir_from_argv, load_runs, mean, subfolders, write_csv  # noqa: E402

KEYS = ("rebuilds", "max_stress_q", "mean_q", "networkx_changes", "max_abs_q_diff", "excluded_initial",
        "initial_nodes", "initial_edges", "final_nodes", "final_edges", "final_communities")


def collect(job_dir: Path) -> list:
    rows = []
    for folder in subfolders(job_dir):
        for config, entry in load_runs(folder, with_rows=False).items():
            for rep in entry["reps"]:
                s, meta = rep["summary"], rep["meta"]
                rows.append({
                    "job": job_dir.name, "dataset": folder.name, "config": config, "rep": s["rep"],
                    "plan_seed": s["plan_seed"], "louvain_seed": s["louvain_seed"],
                    "stress_threshold": meta["params"]["stress_threshold"], "changes": s["changes"],
                    **{k: s[k] for k in KEYS}, "node": meta["node"], "commit": meta["commit"],
                    "timing": meta["timing"],
                })
    return rows


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    job_dir = job_dir_from_argv(argv, "d3")
    rows = collect(job_dir)
    if not rows:
        sys.exit(f"no hay *_meta.json en {job_dir}")
    out = Path(argv[1]) if len(argv) > 1 else RESULTS / "d3_dinamica.csv"
    write_csv(out, rows)

    print(f"{job_dir} -> {out}  (commit {rows[0]['commit']}, promedio entre repeticiones)")
    hdr = (f"{'conjunto':<24} {'configuracion':<24} {'reps':>4} {'umbral':>7} {'recons':>7} "
           f"{'estres max':>10} {'Q media':>8} {'|dQ| max':>9}")
    print(hdr)
    print("-" * len(hdr))
    groups = {}
    for row in rows:
        groups.setdefault((row["dataset"], row["config"]), []).append(row)
    for (dataset, config), group in sorted(groups.items()):
        diffs = [r["max_abs_q_diff"] for r in group if r["max_abs_q_diff"] is not None]
        print(f"{dataset:<24} {config:<24} {len(group):>4} {group[0]['stress_threshold']:>7g} "
              f"{mean(r['rebuilds'] for r in group):>7.1f} {mean(r['max_stress_q'] for r in group):>10.3f} "
              f"{mean(r['mean_q'] for r in group):>8.4f} {max(diffs, default=float('nan')):>9.1e}")
    print("\nlos tiempos de D3 no se reportan (amd256, TIMING=0); salen de D2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
