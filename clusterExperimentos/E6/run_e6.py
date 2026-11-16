#!/usr/bin/env python3
"""
E6: reacomodo de comunidades (reconstruccion por estres) en DEvAC.

E5 mostro que modificar politicas desordena las comunidades y el lector se vuelve mas lento. El
monitor suma un estres (|dQ| acumulado) y, al pasar el umbral tau, reconstruye todo: grafo,
Louvain, indices y Q. Por repeticion:

  parte A, recuperacion (sin concurrencia): se aplican n_ops modificaciones con el reacomodo
      apagado, se mide el lector (degradado), se reacomoda una vez y se vuelve a medir
      (recuperado). Tambien el costo del reacomodo por etapa y que no cambie ninguna decision.
  parte B, concurrente, una vez por cada tau: un hilo escritor aplica las mismas modificaciones
      con el reacomodo encendido mientras un hilo lector evalua sin parar. Como en la API, las
      escrituras esperan a que termine el reacomodo (un solo escritor) y las lecturas no esperan.
      Se mide cuantos reacomodos hubo, cuanto duraron, la lectura durante el reacomodo, si hubo
      decisiones inconsistentes y como queda el lector al final.

Uso:
  python run_e6.py --n 2000 --reps 10 --out-dir resultados/e6
"""
import argparse
import csv
import gc
import json
import math
import os
import socket
import statistics as st
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "E5"))
sys.path.insert(0, str(HERE.parent / "E2"))
sys.path.insert(0, str(HERE.parent / "E1"))

import run_e5 as e5  # noqa: E402  (trae run_e1, generate_w4, make_ops, oraculo, etc.)
from run_e5 import pct, quiet, run_e1  # noqa: E402
from gen_w4 import DEFAULTS as W4_DEFAULTS, MODES as W4_MODES, generate_w4, variant_label  # noqa: E402
from devac import CommunityDetector, CommunityPolicyEvaluator, GraphBuilder, Policy  # noqa: E402
from devac.monitor import ModularityMonitor  # noqa: E402

clock = time.perf_counter_ns


def new_engine(base, theta, seed, tau):
    pols = e5.copy.deepcopy(base)
    ev = CommunityPolicyEvaluator()
    mon = ModularityMonitor(ev, GraphBuilder(), CommunityDetector(seed=seed), threshold=tau,
                            similarity_threshold=theta, audit_policies=False)
    with quiet():
        ev.run(pols, {})
        mon.rebuild_now()  # mismo arranque que la API
    return ev, mon


def read_p95(ev, reqs, n):
    lat = np.empty(n, dtype=np.int64)
    gc.collect()
    for k in range(n):
        r = reqs[k % len(reqs)]
        t0 = clock()
        ev.evaluate(r)
        lat[k] = clock() - t0
    return pct(lat / 1000.0, 95)


def decisions(ev, reqs):
    return [ev.evaluate(r)["result"] for r in reqs]


def oracle_ok(dec, D_row):
    return 100.0 * sum(d == ("allow" if D_row[i] else "deny") for i, d in enumerate(dec)) / len(dec)


# ---------------------------------------------------------------- parte A
def run_recovery(base, ops, theta, seed, reqs, D, n_rest):
    ev, mon = new_engine(base, theta, seed, math.inf)
    for r in reqs[:200]:
        ev.evaluate(r)
    q0 = mon.q
    rest0 = read_p95(ev, reqs, n_rest)
    errors = Counter()
    with quiet():
        for op in ops:
            try:
                e5.apply_op(ev, op)
            except Exception as e:  # noqa: BLE001
                errors[type(e).__name__] += 1
    q_deg, stress = mon.q, mon.get_stats()["stress"]
    rest_deg = read_p95(ev, reqs, n_rest)
    before = decisions(ev, reqs)
    gc.collect()
    t0 = time.perf_counter()
    with quiet():
        mon.rebuild_now()
    t_rebuild = time.perf_counter() - t0
    after = decisions(ev, reqs)
    rest_rec = read_p95(ev, reqs, n_rest)
    s = mon.last_rebuild_stats
    return {
        "q_inicial": round(q0, 4), "q_degradada": round(q_deg, 4), "q_recuperada": round(mon.q, 4),
        "estres_acumulado": round(stress, 4),
        "lectura_inicial_p95_us": rest0, "lectura_degradada_p95_us": rest_deg, "lectura_recuperada_p95_us": rest_rec,
        "degradacion": round(rest_deg / rest0, 3), "recuperada_vs_inicial": round(rest_rec / rest0, 3),
        "reacomodo_s": round(t_rebuild, 3), "t_grafo_s": round(s.get("t_build_s", 0), 3),
        "t_louvain_s": round(s.get("t_louvain_s", 0), 3), "t_indices_s": round(s.get("t_index_s", 0), 3),
        "t_q_s": round(s.get("t_q_s", 0), 3), "comunidades": s.get("communities"),
        "decisiones_preservadas_pct": round(100.0 * sum(a == b for a, b in zip(before, after)) / len(after), 3),
        "oraculo_antes_pct": round(oracle_ok(before, D[-1]), 3), "oraculo_despues_pct": round(oracle_ok(after, D[-1]), 3),
        "errores": sum(errors.values()),
    }


# ---------------------------------------------------------------- parte B
def run_concurrent(base, ops, theta, seed, tau, reqs, D, cores, n_rest):
    ev, mon = new_engine(base, theta, seed, tau)
    pending = threading.Event()
    mon._trigger_rebuild = pending.set  # como la API: el reacomodo hace fila detras de la escritura en curso
    for r in reqs[:200]:
        ev.evaluate(r)
    rest0 = read_p95(ev, reqs, n_rest)

    op_t = np.zeros((len(ops), 2), dtype=np.int64)
    rebuilds = []  # (tras la operacion k, t0, t1, estadisticas)
    reads = []
    r_err, w_err = Counter(), Counter()
    stop = threading.Event()
    go = threading.Barrier(2)

    def pin(core):
        if core is not None and hasattr(os, "sched_setaffinity"):
            os.sched_setaffinity(0, {core})

    def reader():
        pin(cores[1])
        go.wait()
        i, n = 0, len(reqs)
        while not stop.is_set():
            idx = i % n
            t0 = clock()
            try:
                dec = ev.evaluate(reqs[idx])["result"]
            except Exception as e:  # noqa: BLE001
                dec = None
                r_err[type(e).__name__] += 1
            reads.append((idx, dec, t0, clock()))
            i += 1

    def writer():
        pin(cores[0])
        go.wait()
        for k, op in enumerate(ops):
            op_t[k, 0] = clock()
            try:
                e5.apply_op(ev, op)
            except Exception as e:  # noqa: BLE001
                w_err[type(e).__name__] += 1
            op_t[k, 1] = clock()
            if pending.is_set():  # la siguiente escritura espera a que termine el reacomodo
                t0 = clock()
                try:
                    mon.rebuild_now()
                except Exception as e:  # noqa: BLE001
                    w_err["reacomodo:" + type(e).__name__] += 1
                rebuilds.append((k + 1, t0, clock(), dict(mon.last_rebuild_stats)))
                pending.clear()
        stop.set()

    gc.collect()
    t_start = clock()
    with quiet():
        tr, tw = threading.Thread(target=reader), threading.Thread(target=writer)
        tr.start()
        tw.start()
        tw.join()
        tr.join()
    t_writer = (clock() - t_start) / 1e9

    rest_end = read_p95(ev, reqs, n_rest)

    starts, ends = op_t[:, 0], op_t[:, 1]
    windows = [(t0, t1) for _, t0, t1, _ in rebuilds]
    inconsistent = inc_rebuild = 0
    lat_rebuild, lat_ops, examples = [], [], []
    last = max([ends[-1]] + [b1 for _, b1 in windows])
    for idx, dec, t0, t1 in reads:
        if t1 < starts[0] or t0 > last:
            continue
        in_rb = any(t0 < b1 and t1 > b0 for b0, b1 in windows)
        k_lo = int(np.searchsorted(ends, t0, side="left"))
        k_hi = int(np.searchsorted(starts, t1, side="left"))
        if in_rb:
            lat_rebuild.append(t1 - t0)
        elif k_hi > k_lo:
            lat_ops.append(t1 - t0)
        if dec is None:
            continue
        if not np.any(D[k_lo:k_hi + 1, idx] == (1 if dec == "allow" else 0)):
            inconsistent += 1
            inc_rebuild += in_rb
            if len(examples) < 5:
                examples.append({"solicitud": idx, "decision": dec, "estados": [k_lo, k_hi], "en_reacomodo": in_rb})

    final = decisions(ev, reqs)
    durs = [(t1 - t0) / 1e9 for _, t0, t1, _ in rebuilds]
    lr = np.array(lat_rebuild) / 1000.0
    lo = np.array(lat_ops) / 1000.0
    return {
        "reacomodos": len(rebuilds), "reacomodo_tras_op": [k for k, *_ in rebuilds],
        "reacomodo_p50_s": round(st.median(durs), 3) if durs else "",
        "reacomodo_max_s": round(max(durs), 3) if durs else "",
        "reacomodo_total_s": round(sum(durs), 3),
        "escritor_total_s": round(t_writer, 3),
        "lectura_reposo_p95_us": rest0, "lectura_reposo_final_p95_us": rest_end,
        "deriva_final": round(rest_end / rest0, 3) if rest0 else "",
        "lectura_cambios_p95_us": pct(lo, 95), "lecturas_en_cambios": len(lat_ops),
        "lectura_reacomodo_p50_us": pct(lr, 50), "lectura_reacomodo_p95_us": pct(lr, 95),
        "lecturas_en_reacomodo": len(lat_rebuild),
        "razon_reacomodo_p95": round(pct(lr, 95) / rest0, 3) if len(lr) and rest0 else "",
        "q_final": round(mon.q, 4),
        "inconsistentes": inconsistent, "inconsistentes_en_reacomodo": inc_rebuild,
        "errores_lector": sum(r_err.values()), "errores_escritor": sum(w_err.values()),
        "final_ok_pct": round(oracle_ok(final, D[-1]), 3),
        "_detalle": {"errores_lector": dict(r_err), "errores_escritor": dict(w_err), "ejemplos": examples,
                     "etapas_reacomodo": [s for *_, s in rebuilds]},
    }


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="E6: reacomodo de comunidades por estres")
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--n-ops", type=int, default=1000)
    ap.add_argument("--taus", nargs="+", type=float, default=[0.02, 0.05, 0.1, math.inf],
                    help="umbrales de estres; inf = reacomodo apagado (como E5)")
    ap.add_argument("--data-seed", type=int, default=1)
    ap.add_argument("--mode", choices=W4_MODES, default="uniforme")
    ap.add_argument("--p-mix", type=float, default=W4_DEFAULTS["p_mix"])
    ap.add_argument("--theta", type=float, default=0.3)
    ap.add_argument("--pool", type=int, default=400)
    ap.add_argument("--n-rest", type=int, default=20000)
    ap.add_argument("--cores", default="")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    w4 = {**W4_DEFAULTS, "mode": args.mode, "p_mix": args.p_mix}
    carga = f"W4-{variant_label(w4)}"
    base = [Policy.model_validate(p) for p in generate_w4(args.n, seed=args.data_seed, **w4)]
    if args.cores:
        cores = [int(c) for c in args.cores.split(",")]
    elif hasattr(os, "sched_getaffinity") and len(os.sched_getaffinity(0)) >= 2:
        cores = e5.two_physical_cores(sorted(os.sched_getaffinity(0)))
    else:
        cores = [None, None]
    tag = f"e6_{carga}_n{args.n}"
    log = lambda m: print(f"[E6 {carga} n={args.n}] {m}", flush=True)
    log(f"nucleos escritor/lector: {cores}, taus: {args.taus}")

    rows_a, rows_b, details = [], [], []
    for rep in range(args.reps):
        ops = e5.make_ops("update", base, rep, args.n_ops, w4)
        pool = e5.request_pool(base, ops, rep, args.pool)
        reqs = [run_e1.to_access_request(v) for v in pool]
        D = e5.reference_states(base, ops, pool)
        seed = rep + 1

        a = run_recovery(base, ops, args.theta, seed, reqs, D, args.n_rest)
        rows_a.append({"carga": carga, "n": args.n, "rep": rep, **a})
        log(f"rep {rep + 1}/{args.reps} recuperacion: Q {a['q_inicial']} -> {a['q_degradada']} -> {a['q_recuperada']} | "
            f"lectura p95 {a['lectura_inicial_p95_us']} -> {a['lectura_degradada_p95_us']} -> "
            f"{a['lectura_recuperada_p95_us']} us | reacomodo {a['reacomodo_s']} s | "
            f"decisiones preservadas {a['decisiones_preservadas_pct']}%")

        for tau in args.taus:
            b = run_concurrent(base, ops, args.theta, seed, tau, reqs, D, cores, args.n_rest)
            det = b.pop("_detalle")
            row = {"carga": carga, "n": args.n, "rep": rep, "tau": "inf" if math.isinf(tau) else tau, **b}
            row["reacomodo_tras_op"] = " ".join(map(str, row["reacomodo_tras_op"]))
            rows_b.append(row)
            details.append({"rep": rep, "tau": row["tau"], **det})
            log(f"rep {rep + 1}/{args.reps} tau={row['tau']}: {b['reacomodos']} reacomodos "
                f"(p50 {b['reacomodo_p50_s']} s) | lectura p95 reposo={b['lectura_reposo_p95_us']} "
                f"final={b['lectura_reposo_final_p95_us']} durante reacomodo={b['lectura_reacomodo_p95_us']} us | "
                f"inconsistentes={b['inconsistentes']} errores={b['errores_lector']}/{b['errores_escritor']}")

    for name, rows in (("recuperacion", rows_a), ("concurrente", rows_b)):
        with open(out_dir / f"{tag}_{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in rows for k in r)), restval="")
            w.writeheader()
            w.writerows(rows)

    def med(rows, k):
        v = [r[k] for r in rows if r[k] != ""]
        return st.median(v) if v else None

    res_a = {k: med(rows_a, k) for k in rows_a[0] if k not in ("carga", "n", "rep")}
    res_b = {}
    for tau in args.taus:
        t = "inf" if math.isinf(tau) else tau
        r = [x for x in rows_b if x["tau"] == t]
        res_b[str(t)] = {k: med(r, k) for k in ("reacomodos", "reacomodo_p50_s", "reacomodo_max_s", "reacomodo_total_s",
                                                 "escritor_total_s", "lectura_reposo_p95_us",
                                                 "lectura_reposo_final_p95_us", "deriva_final", "lectura_cambios_p95_us",
                                                 "lectura_reacomodo_p95_us", "razon_reacomodo_p95", "q_final")}
        res_b[str(t)].update({k: sum(x[k] for x in r) for k in ("lecturas_en_cambios", "lecturas_en_reacomodo",
                                                                  "inconsistentes", "inconsistentes_en_reacomodo",
                                                                  "errores_lector", "errores_escritor")})
        res_b[str(t)]["final_ok_min_pct"] = min(x["final_ok_pct"] for x in r)
    meta = {
        "experimento": "E6", "carga": carga, "n_eventos": args.n, "repeticiones": args.reps, "n_ops": args.n_ops,
        "operacion": "update", "taus": [str(t) for t in args.taus], "theta": args.theta, "pool": args.pool,
        "n_reposo": args.n_rest, "semilla_datos": args.data_seed, "parametros_w4": w4,
        "nucleos_escritor_lector": cores, "resumen_recuperacion": res_a, "resumen_concurrente": res_b,
        "detalle": details,
        "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "nodo": socket.gethostname(), "cpu": run_e1.cpu_model(), "python": sys.version.split()[0],
        "dependencias": run_e1.versions(), "commit_demo": os.environ.get("DEVAC_COMMIT", "desconocido"),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"), "slurm_array_job_id": os.environ.get("SLURM_ARRAY_JOB_ID"),
        "slurm_array_task_id": os.environ.get("SLURM_ARRAY_TASK_ID"),
    }
    with open(out_dir / f"{tag}_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False, default=str)
    log("listo")


if __name__ == "__main__":
    main()
