#!/usr/bin/env python3
# compara dos corridas de D3 con la misma configuracion en nodos distintos: diferencia maxima en las
# columnas de Q y si coinciden reconstrucciones y tamano del grafo, cambio por cambio
#
# uso:
#   python3 compara_repro.py resultados/crudo/d3_repro/<JOB_A> resultados/crudo/d3_repro/<JOB_B> [--csv salida.csv]

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datos import load_runs, subfolders  # noqa: E402

Q_COLUMNS = ("q_incremental", "q_networkx", "delta_q", "stress_q")
EXACT_COLUMNS = ("rebuilds", "nodes", "edges", "change", "policy_id")


def compare(rows_a, rows_b) -> dict:
    out = {"rows_a": len(rows_a), "rows_b": len(rows_b)}
    for col in Q_COLUMNS:
        diffs = [abs(a[col] - b[col]) for a, b in zip(rows_a, rows_b) if a[col] is not None and b[col] is not None]
        out[f"max_abs_{col}"] = max(diffs, default=0.0)
    for col in EXACT_COLUMNS:
        out[f"{col}_mismatches"] = sum(a.get(col) != b.get(col) for a, b in zip(rows_a, rows_b))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="diferencias entre dos corridas de D3")
    ap.add_argument("job_a", type=Path)
    ap.add_argument("job_b", type=Path)
    ap.add_argument("--csv", type=Path)
    args = ap.parse_args(argv)

    results = []
    for folder_a in subfolders(args.job_a):
        folder_b = args.job_b / folder_a.name
        if not folder_b.is_dir():
            sys.exit(f"falta {folder_b}")
        runs_a, runs_b = load_runs(folder_a), load_runs(folder_b)
        for config in sorted(runs_a):
            reps_b = {r["file"]: r for r in runs_b.get(config, {"reps": []})["reps"]}
            for rep_a in runs_a[config]["reps"]:
                rep_b = reps_b.get(rep_a["file"])
                if rep_b is None:
                    sys.exit(f"{folder_b}: falta {rep_a['file']}")
                results.append({
                    "dataset": folder_a.name, "file": rep_a["file"],
                    "node_a": rep_a["meta"]["node"], "cpu_a": rep_a["meta"]["cpu"],
                    "node_b": rep_b["meta"]["node"], "cpu_b": rep_b["meta"]["cpu"],
                    "same_commit": rep_a["meta"]["commit"] == rep_b["meta"]["commit"],
                    **compare(rep_a["rows"], rep_b["rows"]),
                })
    if not results:
        sys.exit(f"no hay corridas que comparar en {args.job_a}")

    worst = {k: max(r[k] for r in results) for k in results[0]
             if k.startswith("max_abs_") or k.endswith("_mismatches")}
    worst["corridas con otro numero de cambios"] = sum(r["rows_a"] != r["rows_b"] for r in results)
    print(f"{args.job_a.name} ({results[0]['node_a']}, {results[0]['cpu_a']})")
    print(f"{args.job_b.name} ({results[0]['node_b']}, {results[0]['cpu_b']})")
    print(f"{len(results)} corridas comparadas; mismo commit en todas: {all(r['same_commit'] for r in results)}")
    for key, value in worst.items():
        print(f"  {key:<36} {value:.3e}" if isinstance(value, float) else f"  {key:<36} {value}")

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(results[0]))
            writer.writeheader()
            writer.writerows(results)
        print(f"-> {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
