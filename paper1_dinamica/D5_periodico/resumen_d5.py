#!/usr/bin/env python3
# D5: reconstruccion periodica (cada k cambios) contra la regla de estres, con los mismos cambios.
# por corrida: reconstrucciones, Q promedio, Q minima y costo. el costo principal es el numero de
# reconstrucciones y los nodos + aristas reconstruidos (no dependen de la maquina); los segundos son una
# estimacion con los tiempos de D2 (resultados/d2_escalamiento.csv), si ya existe.
# junta el job de d5_periodico.sbatch y el de d5_grande.sbatch. escribe resultados/d5_periodico.csv
#
# uso:
#   python3 paper1_dinamica/D5_periodico/resumen_d5.py        (jobs mas recientes de crudo/d5 y crudo/d5_grande)
#   python3 paper1_dinamica/D5_periodico/resumen_d5.py crudo/d5/<JOB> crudo/d5_grande/<JOB> [--out x.csv] [--d2 d2.csv]

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datos import (RESULTS, ci95, cost_model, estimate_cost, latest_job, load_runs, mean,  # noqa: E402
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


def default_jobs() -> list:
    return [job for job in (latest_job("d5"), latest_job("d5_grande")) if job is not None]


def collect(job_dirs, model=None) -> list:
    rows = []
    for job_dir, folder in ((j, f) for j in job_dirs for f in subfolders(j)):
        if folder.name == "redes":
            continue
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
                    # costo principal, sin depender de la maquina: nodos + aristas de los grafos reconstruidos
                    "rebuilt_size": s["rebuilt_size"], "mean_size": s["mean_size"],
                    # secundario: segundos estimados con los tiempos de D2 (node422)
                    "est_rebuild_s": None, "est_update_s": None, "est_cost_s": None,
                    # medido en amd256 (timing_not_reported): no va al paper
                    "total_cost_s": s["total_update_s"] + s["total_rebuild_s"],
                    "audit": meta.get("audit"),
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
    ap.add_argument("job_dirs", nargs="*", help="por defecto los mas recientes de crudo/d5 y crudo/d5_grande")
    ap.add_argument("--out", default=str(RESULTS / "d5_periodico.csv"))
    ap.add_argument("--d2", default=str(RESULTS / "d2_escalamiento.csv"),
                    help="resumen de D2 para estimar el costo en segundos")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    job_dirs = [Path(j) for j in args.job_dirs] or default_jobs()
    if not job_dirs or not all(j.is_dir() for j in job_dirs):
        sys.exit("no hay resultados de D5 (resultados/crudo/d5/<JOB> o crudo/d5_grande/<JOB>)")
    model = cost_model(Path(args.d2))
    rows = collect(job_dirs, model)
    if not rows:
        sys.exit(f"no hay *_meta.json en {', '.join(map(str, job_dirs))}")
    write_csv(Path(args.out), rows)

    print(f"{', '.join(map(str, job_dirs))} -> {args.out}  (commit {rows[0]['commit']})")
    if any(r["audit"] is not False for r in rows):
        print("AVISO: hay corridas con auditoria (o sin el campo audit); el paper 1 corre sin ella")
    if model is None:
        print(f"sin {args.d2}: los segundos estimados quedan vacios; corre antes resumen_d2.py")
    else:
        (cr, br), (cu, bu) = model["rebuild"], model["update"]
        print(f"segundos estimados con D2 ({model['source']}): reconstruccion = {cr:.2e} * x^{br:.2f}, "
              f"cambio = {cu:.2e} * x^{bu:.2f} (x = nodos + aristas)")
        if model["timing"] != ["reported"]:
            print("AVISO: el resumen de D2 trae corridas sin TIMING=1")
    for (dataset, scenario), by_strategy in groups(rows).items():
        print(f"\n{dataset}, {scenario}")
        hdr = (f"  {'estrategia':<16} {'reps':>4} {'recons':>13} {'nodos+aristas recons':>21} {'Q media':>17} "
               f"{'Q min':>8} {'s estimados':>15}")
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for name, group in by_strategy.items():
            rebuilds = [r["rebuilds"] for r in group]
            q = [r["mean_q"] for r in group]
            cost = [r["est_cost_s"] for r in group]
            est = f"{mean(cost):>7.1f} ± {ci95(cost):<5.1f}" if model else f"{'-':>15}"
            print(f"  {name:<16} {len(group):>4} {mean(rebuilds):>6.1f} ± {ci95(rebuilds):<4.1f} "
                  f"{mean(r['rebuilt_size'] for r in group):>21,.0f} "
                  f"{mean(q):>7.4f} ± {ci95(q):<7.4f} {min(r['min_q'] for r in group):>8.4f} {est}")
    print("\n± = IC95 entre repeticiones. 'Q min' = la minima en todas las repeticiones."
          "\nCosto: 'recons' y 'nodos+aristas recons' (no dependen de la maquina). 's estimados' es una"
          "\nestimacion (reconstrucciones + cambios con los tiempos de D2 en node422); total_cost_s del csv"
          "\nviene de amd256 (timing_not_reported).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
