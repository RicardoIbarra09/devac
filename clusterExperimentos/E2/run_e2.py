#!/usr/bin/env python3
"""
E2: latencia de decision de DEvAC contra el evaluador lineal, segun el tamano n.

Para un tamano n y una semilla:
  1. Genera la carga W4 con n eventos (gen_w4.py) y construye la estructura de
     comunidades (el tiempo de construccion se registra solo como referencia; E3 lo
     mide con su propio protocolo).
  2. Genera n_warmup + n_measure solicitudes (mitad con coincidencia, mitad sin ella)
     con el mismo generador de E1, y la decision del oraculo para cada una.
  3. Crea todos los AccessRequest ANTES de medir (pydantic queda fuera del tiempo).
  4. Calentamiento: n_warmup solicitudes descartadas en cada evaluador.
  5. Por cada repeticion: gc.collect(), gc desactivado durante la ventana, y se mide
     cada solicitud por separado con perf_counter_ns(). El orden DEvAC/lineal se
     alterna entre repeticiones para no favorecer a ninguno.
  6. Guarda un CSV de resumen (una fila por repeticion y evaluador), las latencias
     crudas en .npz y un meta.json.

Uso:
  python run_e2.py --n 2000 --seed 1 --reps 30 --out-dir resultados/e2
"""
import argparse
import copy
import csv
import gc
import json
import os
import resource
import socket
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "E1"))

import run_e1  # noqa: E402  (al importarlo desactiva la exportacion GEXF)
from gen_w4 import DEFAULTS as W4_DEFAULTS, generate_w4  # noqa: E402
from devac import (  # noqa: E402
    CommunityPolicyEvaluator,
    Policy,
    TraditionalPolicyEvaluator,
)


def percentile(a, q):
    return float(np.percentile(a, q)) if len(a) else float("nan")


def measure(evaluator, requests):
    """Latencia (ns), pasos y decision por solicitud."""
    lat = np.empty(len(requests), dtype=np.int64)
    steps = np.full(len(requests), -1, dtype=np.int64)
    decisions = []
    clock = time.perf_counter_ns
    gc.collect()
    gc.disable()
    try:
        for i, req in enumerate(requests):
            t0 = clock()
            r = evaluator.evaluate(req)
            lat[i] = clock() - t0
            s = r.get("steps")
            if s is not None:
                steps[i] = s
            decisions.append(r["result"])
    finally:
        gc.enable()
    return lat, steps, decisions


def evaluator_memory_mb(pols, pbc):
    """Memoria que reservan los indices de DEvAC al construirse (tracemalloc)."""
    pols_copy = copy.deepcopy(pols)
    pbc_copy = copy.deepcopy(pbc)
    gc.collect()
    tracemalloc.start()
    ev = CommunityPolicyEvaluator()
    ev.run(pols_copy, pbc_copy)
    current, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del ev
    return current / 2**20


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, required=True, help="Numero de eventos de la carga W4")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--reps", type=int, default=30)
    ap.add_argument("--n-warmup", type=int, default=1000)
    ap.add_argument("--n-measure", type=int, default=2000)
    ap.add_argument("--no-oracle", action="store_true", help="No calcular la concordancia con el oraculo")
    ap.add_argument("--save-policies", action="store_true", help="Guardar el JSON de W4 en out-dir")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"e2_n{args.n}_s{args.seed}"
    log = lambda m: print(f"[E2 n={args.n}] {m}", flush=True)

    # 1. Carga y estructura
    raw = generate_w4(args.n, seed=args.seed)
    if args.save_policies:
        with open(out_dir / f"w4_n{args.n}_s{args.seed}.json", "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False)
    base = [Policy.model_validate(p) for p in raw]
    pols = copy.deepcopy(base)

    run_e1.random.seed(args.seed)  # Louvain usa el random global del proceso
    t0 = time.perf_counter()
    pbc = run_e1.build_structure(pols)
    build_s = time.perf_counter() - t0
    log(f"estructura: {len(pbc)} comunidades en {build_s:.2f} s")

    devac = CommunityPolicyEvaluator()
    devac.run(pols, pbc)
    linear = TraditionalPolicyEvaluator(copy.deepcopy(pols))

    # 2. Solicitudes y oraculo
    t0 = time.perf_counter()
    compiled = run_e1.compile_policies(base)
    total = args.n_warmup + args.n_measure
    values, failed = run_e1.generate_requests(base, compiled, total, args.seed)
    warm_vals, meas_vals = values[: args.n_warmup], values[args.n_warmup:]
    truth = None
    if not args.no_oracle:
        truth = [run_e1.oracle_decision(compiled, v)[0] for v in meas_vals]
    log(f"solicitudes y oraculo en {time.perf_counter() - t0:.1f} s (fallos de generacion: {failed})")

    # 3. AccessRequest creados antes de medir (una lista por evaluador)
    reqs = {
        "devac": ([run_e1.to_access_request(v) for v in warm_vals],
                  [run_e1.to_access_request(v) for v in meas_vals]),
        "lineal": ([run_e1.to_access_request(v) for v in warm_vals],
                   [run_e1.to_access_request(v) for v in meas_vals]),
    }
    evaluators = {"devac": devac, "lineal": linear}

    # 4. Calentamiento
    for name, ev in evaluators.items():
        for r in reqs[name][0]:
            ev.evaluate(r)

    # 5. Repeticiones
    rows, raw_lat, raw_steps = [], {}, {}
    first_decisions = {}
    for rep in range(args.reps):
        order = ["devac", "lineal"] if rep % 2 == 0 else ["lineal", "devac"]
        for name in order:
            lat, steps, dec = measure(evaluators[name], reqs[name][1])
            raw_lat[f"{name}_rep{rep}"] = lat
            if rep == 0:
                raw_steps[name] = steps
                first_decisions[name] = dec
            lat_us = lat / 1000.0
            valid_steps = steps[steps >= 0]
            rows.append({
                "n": args.n, "seed": args.seed, "rep": rep, "evaluator": name,
                "n_requests": len(lat),
                "lat_mean_us": round(float(lat_us.mean()), 3),
                "lat_p50_us": round(percentile(lat_us, 50), 3),
                "lat_p95_us": round(percentile(lat_us, 95), 3),
                "lat_p99_us": round(percentile(lat_us, 99), 3),
                "steps_mean": round(float(valid_steps.mean()), 3) if len(valid_steps) else "",
                "steps_p99": round(percentile(valid_steps, 99), 3) if len(valid_steps) else "",
                "early_deny_pct": round(100.0 * float((valid_steps == 0).mean()), 3) if len(valid_steps) else "",
            })
        if rep == 0 or (rep + 1) % 10 == 0:
            d = [r for r in rows if r["rep"] == rep]
            p50 = {r["evaluator"]: r["lat_p50_us"] for r in d}
            log(f"rep {rep + 1}/{args.reps}: p50 devac={p50['devac']} us, lineal={p50['lineal']} us")

    # Speedup por repeticion (latencia media y p50 del lineal entre las de DEvAC)
    by_rep = {}
    for r in rows:
        by_rep.setdefault(r["rep"], {})[r["evaluator"]] = r
    for r in rows:
        pair = by_rep[r["rep"]]
        r["speedup_mean"] = round(pair["lineal"]["lat_mean_us"] / pair["devac"]["lat_mean_us"], 3)
        r["speedup_p50"] = round(pair["lineal"]["lat_p50_us"] / pair["devac"]["lat_p50_us"], 3)

    # 6. Concordancia y memoria
    d_dev, d_lin = first_decisions["devac"], first_decisions["lineal"]
    agree_lin = 100.0 * sum(a == b for a, b in zip(d_dev, d_lin)) / len(d_dev)
    agree_orc = (100.0 * sum(a == b for a, b in zip(d_dev, truth)) / len(d_dev)) if truth else None
    agree_lin_orc = (100.0 * sum(a == b for a, b in zip(d_lin, truth)) / len(d_lin)) if truth else None
    fp = sum(a == "allow" and t == "deny" for a, t in zip(d_dev, truth)) if truth else None
    mem_mb = evaluator_memory_mb(pols, pbc)
    peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

    csv_path = out_dir / f"{tag}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    np.savez_compressed(out_dir / f"{tag}_raw.npz",
                        **{f"lat_{k}": v for k, v in raw_lat.items()},
                        **{f"steps_{k}": v for k, v in raw_steps.items()})

    def med(col, ev):
        return float(np.median([r[col] for r in rows if r["evaluator"] == ev]))

    resumen = {
        "p50_devac_us": med("lat_p50_us", "devac"), "p50_lineal_us": med("lat_p50_us", "lineal"),
        "p95_devac_us": med("lat_p95_us", "devac"), "p95_lineal_us": med("lat_p95_us", "lineal"),
        "p99_devac_us": med("lat_p99_us", "devac"), "p99_lineal_us": med("lat_p99_us", "lineal"),
        "speedup_mean_mediana": med("speedup_mean", "devac"),
        "concordancia_lineal_pct": round(agree_lin, 4),
        "concordancia_devac_oraculo_pct": round(agree_orc, 4) if agree_orc is not None else None,
        "concordancia_lineal_oraculo_pct": round(agree_lin_orc, 4) if agree_lin_orc is not None else None,
        "fp_devac": fp,
        "memoria_indices_devac_mb": round(mem_mb, 2),
        "rss_pico_proceso_mb": round(peak_rss_mb, 1),
    }
    meta = {
        "experimento": "E2", "carga": "W4", "n_eventos": args.n, "semilla": args.seed,
        "repeticiones": args.reps, "n_calentamiento": args.n_warmup, "n_medidas": args.n_measure,
        "parametros_w4": W4_DEFAULTS,
        "n_politicas": len(base), "n_deny": sum(p.effect == "deny" for p in base),
        "n_comunidades": len(pbc), "construccion_s": round(build_s, 3),
        "fallos_generacion_solicitudes": failed,
        "resumen": resumen,
        "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "nodo": socket.gethostname(), "cpu": run_e1.cpu_model(),
        "afinidad_cpu": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        "python": sys.version.split()[0], "dependencias": run_e1.versions(),
        "commit_demo": os.environ.get("DEVAC_COMMIT", "desconocido"),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "slurm_array_job_id": os.environ.get("SLURM_ARRAY_JOB_ID"),
        "slurm_array_task_id": os.environ.get("SLURM_ARRAY_TASK_ID"),
    }
    with open(out_dir / f"{tag}_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    log("resumen: " + json.dumps(resumen, ensure_ascii=False))
    log(f"resultados: {csv_path}")


if __name__ == "__main__":
    main()
