#!/usr/bin/env python3
# D4: barrido del umbral de estres. por conjunto y umbral: reconstrucciones, Q promedio y costo total
# (actualizaciones incrementales + reconstrucciones). escribe resultados/d4_sensibilidad.csv
#
# uso:
#   python3 paper1_dinamica/D4_sensibilidad/resumen_d4.py                  (job mas reciente de resultados/crudo/d4)
#   python3 paper1_dinamica/D4_sensibilidad/resumen_d4.py resultados/crudo/d4/<JOB>

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datos import RESULTS, ci95, job_dir_from_argv, load_runs, mean, subfolders, write_csv  # noqa: E402


def collect(job_dir: Path) -> list:
    rows = []
    for folder in subfolders(job_dir):
        for config, entry in load_runs(folder, with_rows=False).items():
            for rep in entry["reps"]:
                s, meta = rep["summary"], rep["meta"]
                rows.append({
                    "job": job_dir.name, "dataset": folder.name, "theta": meta["params"]["stress_threshold"],
                    "rep": s["rep"], "plan_seed": s["plan_seed"], "louvain_seed": s["louvain_seed"],
                    "changes": s["changes"], "rebuilds": s["rebuilds"], "mean_q": s["mean_q"],
                    "max_stress_q": s["max_stress_q"],
                    # costo total: cambios incrementales (grafo + Q) + reconstrucciones por umbral
                    "total_cost_s": s["total_update_s"] + s["total_rebuild_s"],
                    "total_update_s": s["total_update_s"], "total_rebuild_s": s["total_rebuild_s"],
                    # el mismo costo sin depender de la maquina: nodos + aristas de los grafos reconstruidos
                    "rebuilt_size": s["rebuilt_size"],
                    "node": meta["node"], "commit": meta["commit"], "timing": meta["timing"],
                })
    return sorted(rows, key=lambda r: (r["dataset"], r["theta"], r["rep"]))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    job_dir = job_dir_from_argv(argv, "d4")
    rows = collect(job_dir)
    if not rows:
        sys.exit(f"no hay *_meta.json en {job_dir}")
    out = Path(argv[1]) if len(argv) > 1 else RESULTS / "d4_sensibilidad.csv"
    write_csv(out, rows)

    print(f"{job_dir} -> {out}  (commit {rows[0]['commit']})")
    hdr = (f"{'conjunto':<24} {'umbral':>7} {'reps':>4} {'recons':>13} {'Q media':>17} "
           f"{'costo s':>15} {'nodos+aristas recons':>21}")
    print(hdr)
    print("-" * len(hdr))
    groups = {}
    for row in rows:
        groups.setdefault((row["dataset"], row["theta"]), []).append(row)
    for (dataset, theta), group in sorted(groups.items()):
        rebuilds = [r["rebuilds"] for r in group]
        q = [r["mean_q"] for r in group]
        cost = [r["total_cost_s"] for r in group]
        print(f"{dataset:<24} {theta:>7g} {len(group):>4} {mean(rebuilds):>6.1f} ± {ci95(rebuilds):<4.1f} "
              f"{mean(q):>7.4f} ± {ci95(q):<7.4f} {mean(cost):>7.1f} ± {ci95(cost):<5.1f} "
              f"{mean(r['rebuilt_size'] for r in group):>21,.0f}")
    print("\n± = IC95 entre repeticiones. 'costo s' viene de amd256 (timing_not_reported): para el paper"
          "\nusa reconstrucciones o nodos+aristas reconstruidos, o multiplica por los tiempos de D2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
