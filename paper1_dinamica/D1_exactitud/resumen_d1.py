#!/usr/bin/env python3
# junta los csv de las tareas de D1 en resultados/d1_exactitud.csv y muestra el error maximo
#
# uso:
#   python3 paper1_dinamica/D1_exactitud/resumen_d1.py                 (job mas reciente de resultados/crudo/d1)
#   python3 paper1_dinamica/D1_exactitud/resumen_d1.py resultados/crudo/d1/<JOB>

import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from datos import RESULTS, latest_job  # noqa: E402
from run_d1 import CSV_COLUMNS  # noqa: E402


def collect(job_dir: Path) -> list:
    rows = []
    for path in sorted(job_dir.rglob("*_r[0-9][0-9].csv")):
        with open(path, newline="", encoding="utf-8") as f:
            rows.extend({"job": job_dir.name, **row} for row in csv.DictReader(f))
    return rows


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    job_dir = Path(argv[0]) if argv else latest_job("d1")
    out = Path(argv[1]) if len(argv) > 1 else RESULTS / "d1_exactitud.csv"
    if job_dir is None or not job_dir.is_dir():
        sys.exit("no hay resultados de D1 (resultados/crudo/d1/<JOB>)")
    rows = collect(job_dir)
    if not rows:
        sys.exit(f"no hay *_rNN.csv en {job_dir}")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["job"] + CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"{job_dir} -> {out}")
    hdr = f"{'conjunto':<26} {'escenario':<13} {'reps':>4} {'cambios':>8} {'|Q inc - Q nx|':>15} " \
          f"{'nodos dif':>9} {'aristas dif':>11} {'|dpeso|':>9} {'|Q - Q recons|':>15}"
    print(hdr)
    print("-" * len(hdr))
    groups = {}
    for row in rows:
        groups.setdefault((row["dataset"], row["scenario"]), []).append(row)
    for (dataset, scenario), group in sorted(groups.items()):
        def worst(key):
            return max(float(r[key]) for r in group)
        print(f"{dataset:<26} {scenario:<13} {len(group):>4} {int(group[0]['changes']):>8} "
              f"{worst('max_abs_q_incremental_vs_networkx'):>15.1e} {sum(int(r['node_mismatches']) for r in group):>9} "
              f"{sum(int(r['edge_mismatches']) for r in group):>11} {worst('max_abs_weight_diff'):>9.1e} "
              f"{worst('max_abs_q_vs_rebuilt_graph'):>15.1e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
