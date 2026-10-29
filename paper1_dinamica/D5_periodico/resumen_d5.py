#!/usr/bin/env python3
# D5: reconstruccion periodica (cada k cambios) contra la regla de estres, con los mismos cambios.
# por corrida: reconstrucciones, Q promedio, Q minima, nodos + aristas reconstruidos y costo total.
# el costo en segundos se estima con los tiempos de D2 (resultados/d2_escalamiento.csv) si ya existe.
# escribe resultados/d5_periodico.csv
#
# uso:
#   python3 paper1_dinamica/D5_periodico/resumen_d5.py                  (job mas reciente de resultados/crudo/d5)
#   python3 paper1_dinamica/D5_periodico/resumen_d5.py resultados/crudo/d5/<JOB> [salida.csv] [--d2 d2.csv]

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datos import (RESULTS, ci95, cost_model, estimate_cost, job_dir_from_argv, load_runs, mean,  # noqa: E402
                   subfolders, write_csv)


def strategy(params: dict):
    """(regla, parametro, etiqueta) a partir de los parametros de run_dynamics"""
    if params.get("rebuild_every"):
        k = params["rebuild_every"]
        return "periodic", k, f"k={k}"
    if params["stress_threshold"] >= 1e6:
        return "none", None, "sin reconstruir"
    theta = params["stress_threshold"]
    return "stress", theta, f"theta={theta:g}"


def collect(job_dir: Path, model=None) -> list:
    rows = []
    for folder in subfolders(job_dir):
        for config, entry in load_runs(folder, with_rows=False).items():
            for rep in entry["reps"]:
                s, meta = rep["summary"], rep["meta"]
                rule, param, name = strategy(meta["params"])
                row = {
                    "job": job_dir.name, "dataset": folder.name, "scenario": meta["params"]["scenario"],
                    "rule": rule, "param": param, "strategy": name, "rep": s["rep"],
                    "plan_seed": s["plan_seed"], "louvain_seed": s["louvain_seed"],
                    "plan_counts": json.dumps(s["plan_counts"], sort_keys=True),
                    "changes": s["changes"], "rebuilds": s["rebuilds"], "mean_q": s["mean_q"], "min_q": s["min_q"],
                    # costo sin depender de la maquina: nodos + aristas de los grafos reconstruidos
                    "rebuilt_size": s["rebuilt_size"], "mean_size": s["mean_size"],
                    # medido en amd256 (timing_not_reported): no va al paper
                    "total_cost_s": s["total_update_s"] + s["total_rebuild_s"],
                    "est_rebuild_s": None, "est_update_s": None, "est_cost_s": None,
                    "node": meta["node"], "commit": meta["commit"], "timing": meta["timing"],
                }
                if model is not None:
                    row.update(estimate_cost(model, s["rebuild_sizes"], s["changes"], s["mean_size"]))
                rows.append(row)
    check_same_changes(rows)
    order = {"periodic": 0, "stress": 1, "none": 2}
    return sorted(rows, key=lambda r: (r["dataset"], r["scenario"], order[r["rule"]], r["param"] or 0, r["rep"]))


# todas las estrategias de una repeticion deben aplicar la misma secuencia de cambios
def check_same_changes(rows: list) -> None:
    plans = {}
    for r in rows:
        plans.setdefault((r["dataset"], r["scenario"], r["rep"]), set()).add((r["plan_seed"], r["plan_counts"]))
    for key, seen in plans.items():
        if len(seen) > 1:
            sys.exit(f"{key}: las estrategias no aplicaron la misma secuencia de cambios ({seen})")


def groups(rows: list) -> dict:
    out = {}
    for r in rows:
        out.setdefault((r["dataset"], r["scenario"]), {}).setdefault(r["strategy"], []).append(r)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="resumen de D5")
    ap.add_argument("job_dir", nargs="?")
    ap.add_argument("out", nargs="?", default=str(RESULTS / "d5_periodico.csv"))
    ap.add_argument("--d2", default=str(RESULTS / "d2_escalamiento.csv"), help="resumen de D2 para el costo en s")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    job_dir = job_dir_from_argv([args.job_dir] if args.job_dir else [], "d5")
    model = cost_model(Path(args.d2))
    rows = collect(job_dir, model)
    if not rows:
        sys.exit(f"no hay *_meta.json en {job_dir}")
    write_csv(Path(args.out), rows)

    print(f"{job_dir} -> {args.out}  (commit {rows[0]['commit']})")
    if model is None:
        print(f"sin {args.d2}: el costo en segundos queda vacio; corre antes resumen_d2.py")
    else:
        (cr, br), (cu, bu) = model["rebuild"], model["update"]
        print(f"costo con D2 ({model['source']}): reconstruccion = {cr:.2e} * x^{br:.2f}, "
              f"cambio = {cu:.2e} * x^{bu:.2f} (x = nodos + aristas)")
        if model["timing"] != ["reported"]:
            print("AVISO: el resumen de D2 trae corridas sin TIMING=1")
    for (dataset, scenario), by_strategy in groups(rows).items():
        print(f"\n{dataset}, {scenario}")
        hdr = (f"  {'estrategia':<16} {'reps':>4} {'recons':>13} {'Q media':>17} {'Q min':>8} "
               f"{'nodos+aristas recons':>21} {'costo D2 s':>15}")
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for name, group in by_strategy.items():
            rebuilds = [r["rebuilds"] for r in group]
            q = [r["mean_q"] for r in group]
            cost = [r["est_cost_s"] for r in group]
            est = f"{mean(cost):>7.1f} ± {ci95(cost):<5.1f}" if model else f"{'-':>15}"
            print(f"  {name:<16} {len(group):>4} {mean(rebuilds):>6.1f} ± {ci95(rebuilds):<4.1f} "
                  f"{mean(q):>7.4f} ± {ci95(q):<7.4f} {min(r['min_q'] for r in group):>8.4f} "
                  f"{mean(r['rebuilt_size'] for r in group):>21,.0f} {est}")
    print("\n± = IC95 entre repeticiones. 'Q min' = la minima en todas las repeticiones. 'costo D2 s' ="
          "\nreconstrucciones + cambios incrementales con los tiempos de D2 (node422); total_cost_s del csv"
          "\nviene de amd256 (timing_not_reported).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
