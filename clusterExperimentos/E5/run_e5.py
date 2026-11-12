#!/usr/bin/env python3
"""
E5: cambios de politicas en ejecucion (add, remove, update, toggle) y lecturas concurrentes.

toggle = desactivar n_ops/2 politicas al azar y despues activar esas mismas (suspender y devolver).

Por repeticion y tipo de operacion se arma el motor como lo arma la API (evaluador + monitor de
modularidad, sin reconstrucciones automaticas) y se aplican n_ops operaciones de ese tipo:

  modo A, sin lecturas: latencia de cada operacion, separada en evaluador y monitor (grafo + Q).
  modo B, con lecturas: un hilo escritor aplica las mismas operaciones mientras un hilo lector
      evalua solicitudes sin parar, cada hilo en su propio nucleo. Antes se mide la lectura en
      reposo (sin escritor) para comparar.

Una decision es inconsistente si no coincide con la de ningun estado que el lector pudo ver: el
estado antes del primer cambio en curso cuando empezo la lectura, el de despues del ultimo cambio
que empezo antes de terminarla, o cualquiera intermedio. Los estados se calculan aparte con el
oraculo de E1 (deny gana; sin cobertura, deny), sin tocar DEvAC.

Uso:
  python run_e5.py --n 2000 --reps 10 --out-dir resultados/e5
"""
import argparse
import contextlib
import copy
import csv
import gc
import json
import os
import random
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
sys.path.insert(0, str(HERE.parent / "E2"))
sys.path.insert(0, str(HERE.parent / "E1"))

import run_e1  # noqa: E402  (al importarlo desactiva la exportacion gexf)
from gen_w4 import DEFAULTS as W4_DEFAULTS, MODES as W4_MODES, generate_w4, variant_label  # noqa: E402
from devac import CommunityDetector, CommunityPolicyEvaluator, GraphBuilder, Policy  # noqa: E402
from devac.monitor import ModularityMonitor  # noqa: E402

OPS = ("add", "remove", "update", "toggle")
clock = time.perf_counter_ns


class _Null:
    def write(self, *_):
        return 0

    def flush(self):
        pass


def quiet():
    # add/remove/update imprimen en cada llamada; escribir en pantalla no debe entrar en las latencias
    return contextlib.redirect_stdout(_Null())


def pct(a, q):
    return round(float(np.percentile(a, q)), 3) if len(a) else ""


# ---------------------------------------------------------------- motor
def new_engine(base, theta, seed):
    pols = copy.deepcopy(base)
    ev = CommunityPolicyEvaluator()
    mon = ModularityMonitor(ev, GraphBuilder(), CommunityDetector(), threshold=float("inf"),
                            similarity_threshold=theta, audit_policies=False)
    with quiet():
        ev.run(pols, {})
        random.seed(seed)  # louvain usa el random global
        mon.rebuild_now()  # mismo arranque que la API
    return ev, mon


def apply_op(ev, op):
    kind, old, new = op
    if kind == "add":
        ev.add_policy(copy.deepcopy(new))
    elif kind == "remove":
        ev.remove_policy(old.policy_id)
    elif kind == "deactivate":
        ev.deactivate_policy(old.policy_id)
    elif kind == "activate":
        ev.activate_policy(old.policy_id)
    else:
        ev.update_policy(copy.deepcopy(new))


# ---------------------------------------------------------------- operaciones
def make_ops(kind, base, rep, n_ops, w4_params):
    rng = random.Random(1000 * rep + OPS.index(kind))
    by_id = {p.policy_id: p for p in base}
    if kind == "add":
        raw = generate_w4(n_ops, seed=5000 + rep, **w4_params)
        ops = []
        for i, p in enumerate(raw):
            pid = f"e5-add-r{rep}-{i:05d}"
            p["policy_id"] = pid
            for j, e in enumerate(p["events"]):
                e["event_id"] = f"{pid}-e{j + 1}"
            ops.append(("add", None, Policy.model_validate(p)))
        return ops
    if kind == "toggle":
        # suspender y devolver: primero se desactivan todas, luego se activan en el mismo orden
        ids = rng.sample(sorted(by_id), n_ops // 2)
        off = {pid: by_id[pid].model_copy(update={"active": False}) for pid in ids}
        return ([("deactivate", by_id[pid], off[pid]) for pid in ids]
                + [("activate", off[pid], by_id[pid]) for pid in ids])
    ids = rng.sample(sorted(by_id), n_ops)
    if kind == "remove":
        return [("remove", by_id[pid], None) for pid in ids]
    # update: misma politica y mismo evento, con los atributos de un evento W4 nuevo
    fresh = [Policy.model_validate(p) for p in generate_w4(n_ops, seed=6000 + rep, **w4_params)]
    ops = []
    for pid, f in zip(ids, fresh):
        old = by_id[pid]
        new = copy.deepcopy(old)
        for e_new, e_src in zip(new.events, f.events * len(new.events)):
            for fld, _ in run_e1.FIELDS:
                setattr(e_new, fld, copy.deepcopy(getattr(e_src, fld)))
        ops.append(("update", old, new))
    return ops


def request_pool(base, ops, rep, n_pool):
    # mitad solicitudes generales, mitad dirigidas a las politicas que se van a cambiar
    rng = random.Random(7000 + rep)
    compiled = run_e1.compile_policies(base)
    general, _ = run_e1.generate_requests(base, compiled, n_pool // 2, 7000 + rep)
    vocab = run_e1.build_vocab(base)
    touched = [e for _, old, new in ops for p in (old, new) if p is not None for e in p.events]
    targeted = []
    while len(targeted) < n_pool - len(general):
        v = run_e1._matching_request(rng, touched, vocab)
        targeted.append(v if v is not None else general[len(targeted) % len(general)])
    pool = general + targeted
    rng.shuffle(pool)
    return pool


# ---------------------------------------------------------------- estados de referencia (oraculo)
def reference_states(base, ops, pool):
    """D[k, i] = 1 si la solicitud i se permite tras k operaciones (0 = deny)."""
    def covers_all(policy):
        out = np.zeros(len(pool), dtype=bool)
        if policy is None or not policy.active:
            return out
        for e in policy.events:
            comps = [run_e1._compile_component(getattr(e, fld).value) for fld, _ in run_e1.FIELDS]
            for i, vals in enumerate(pool):
                if not out[i] and all(run_e1._covers(c, v) for c, v in zip(comps, vals)):
                    out[i] = True
        return out

    allow = np.zeros(len(pool), dtype=np.int32)
    deny = np.zeros(len(pool), dtype=np.int32)
    for p in base:
        c = covers_all(p)
        if c.any():
            (deny if p.effect == "deny" else allow)[c] += 1
    D = np.zeros((len(ops) + 1, len(pool)), dtype=np.int8)
    D[0] = (deny == 0) & (allow > 0)
    for k, (_, old, new) in enumerate(ops):
        for p, sign in ((old, -1), (new, +1)):
            c = covers_all(p)
            if c.any():
                (deny if p.effect == "deny" else allow)[c] += sign
        D[k + 1] = (deny == 0) & (allow > 0)
    return D


# ---------------------------------------------------------------- modo A
def run_sequential(base, ops, theta, seed, check_pool, D):
    ev, mon = new_engine(base, theta, seed)
    tot, monitor = [], []
    errors = Counter()
    gc.collect()
    with quiet():
        for op in ops:
            h = len(mon.history)
            t0 = clock()
            try:
                apply_op(ev, op)
            except Exception as e:  # noqa: BLE001
                errors[type(e).__name__] += 1
            tot.append((clock() - t0) / 1e6)
            rec = mon.history[-1] if len(mon.history) > h else None
            monitor.append(((rec["t_incremental_s"] or 0) + (rec["t_graph_s"] or 0)) * 1e3 if rec else 0.0)
    final = [ev.evaluate(run_e1.to_access_request(v))["result"] == ("allow" if D[-1, i] else "deny")
             for i, v in enumerate(check_pool)]
    return np.array(tot), np.array(monitor), errors, 100.0 * sum(final) / len(final)


# ---------------------------------------------------------------- modo B
def run_concurrent(base, ops, theta, seed, pool, D, cores, n_rest):
    ev, mon = new_engine(base, theta, seed)
    reqs = [run_e1.to_access_request(v) for v in pool]
    for r in reqs[:200]:
        ev.evaluate(r)  # calentamiento

    # lectura en reposo, sin escritor
    rest = np.empty(n_rest, dtype=np.int64)
    gc.collect()
    for k in range(n_rest):
        r = reqs[k % len(reqs)]
        t0 = clock()
        ev.evaluate(r)
        rest[k] = clock() - t0

    op_t = np.zeros((len(ops), 2), dtype=np.int64)
    reads = []  # (indice, decision o None, t0, t1)
    r_err, w_err = Counter(), Counter()
    stop = threading.Event()
    go = threading.Barrier(2)

    def pin(core):
        if core is not None and hasattr(os, "sched_setaffinity"):
            os.sched_setaffinity(0, {core})  # 0 = el hilo que llama

    def reader():
        pin(cores[1])
        go.wait()
        i = 0
        n = len(reqs)
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
                apply_op(ev, op)
            except Exception as e:  # noqa: BLE001
                w_err[type(e).__name__] += 1
            op_t[k, 1] = clock()
        stop.set()

    gc.collect()
    with quiet():
        tr, tw = threading.Thread(target=reader), threading.Thread(target=writer)
        tr.start()
        tw.start()
        tw.join()
        tr.join()

    # reposo final: mismo lector, sin escritor, sobre el estado que dejaron las operaciones.
    # separa lo que se debe a la concurrencia de lo que se debe a como quedo el estado
    rest_end = np.empty(n_rest, dtype=np.int64)
    gc.collect()
    for k in range(n_rest):
        r = reqs[k % len(reqs)]
        t0 = clock()
        ev.evaluate(r)
        rest_end[k] = clock() - t0

    starts, ends = op_t[:, 0], op_t[:, 1]
    inconsistent, during_lat, n_during = 0, [], 0
    examples = []
    for idx, dec, t0, t1 in reads:
        if t1 < starts[0] or t0 > ends[-1]:
            continue
        k_lo = int(np.searchsorted(ends, t0, side="left"))    # cambios terminados antes de empezar
        k_hi = int(np.searchsorted(starts, t1, side="left"))  # cambios que empezaron antes de terminar
        during_lat.append(t1 - t0)
        overlapped = k_hi > k_lo
        n_during += overlapped
        if dec is None:
            continue
        ok = np.any(D[k_lo:k_hi + 1, idx] == (1 if dec == "allow" else 0))
        if not ok:
            inconsistent += 1
            if len(examples) < 5:
                examples.append({"solicitud": idx, "decision": dec, "estados": [k_lo, k_hi]})

    final = sum(ev.evaluate(r)["result"] == ("allow" if D[-1, i] else "deny") for i, r in enumerate(reqs))
    return {
        "rest_ns": rest, "rest_end_ns": rest_end, "during_ns": np.array(during_lat, dtype=np.int64),
        "op_ms": (ends - starts) / 1e6, "n_reads": len(reads), "n_reads_overlap": n_during,
        "inconsistent": inconsistent, "examples": examples,
        "reader_errors": dict(r_err), "writer_errors": dict(w_err),
        "final_ok_pct": 100.0 * final / len(reqs),
    }


# dos cpus que no sean hilos hermanos del mismo nucleo fisico (hyperthreading)
def two_physical_cores(allowed):
    def topo(cpu):
        base = f"/sys/devices/system/cpu/cpu{cpu}/topology"
        try:
            return (open(f"{base}/physical_package_id").read().strip(), open(f"{base}/core_id").read().strip())
        except OSError:
            return (None, cpu)
    first = allowed[0]
    for cpu in allowed[1:]:
        if topo(cpu) != topo(first):
            return [first, cpu]
    return allowed[:2]


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="E5: operaciones dinamicas y lecturas concurrentes")
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--n-ops", type=int, default=1000)
    ap.add_argument("--ops", nargs="+", choices=OPS, default=list(OPS))
    ap.add_argument("--data-seed", type=int, default=1)
    ap.add_argument("--mode", choices=W4_MODES, default="uniforme")
    ap.add_argument("--p-mix", type=float, default=W4_DEFAULTS["p_mix"])
    ap.add_argument("--theta", type=float, default=0.3, help="umbral de similitud (el valor actual del motor)")
    ap.add_argument("--pool", type=int, default=400, help="solicitudes que evalua el lector")
    ap.add_argument("--n-rest", type=int, default=20000, help="lecturas en reposo antes del escritor")
    ap.add_argument("--cores", default="", help="nucleos para escritor,lector (por defecto los dos primeros)")
    ap.add_argument("--switch-interval", type=float, default=None,
                    help="turno del GIL en segundos (Python usa 0.005); para el control del GIL")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    if args.switch_interval is not None:
        sys.setswitchinterval(args.switch_interval)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    w4_params = {**W4_DEFAULTS, "mode": args.mode, "p_mix": args.p_mix}
    carga = f"W4-{variant_label(w4_params)}"
    base = [Policy.model_validate(p) for p in generate_w4(args.n, seed=args.data_seed, **w4_params)]
    if args.cores:
        cores = [int(c) for c in args.cores.split(",")]
    elif hasattr(os, "sched_getaffinity") and len(os.sched_getaffinity(0)) >= 2:
        cores = two_physical_cores(sorted(os.sched_getaffinity(0)))
    else:
        cores = [None, None]
    # el control del GIL lleva sufijo para no pisar la corrida normal en los agregados
    tag = f"e5_{carga}_n{args.n}" + (f"_gil{args.switch_interval:g}" if args.switch_interval is not None else "")
    log = lambda m: print(f"[E5 {carga} n={args.n}] {m}", flush=True)
    log(f"nucleos escritor/lector: {cores}, intervalo de cambio de hilo del GIL: {sys.getswitchinterval()} s")

    rows, details = [], []
    for rep in range(args.reps):
        for kind in args.ops:
            ops = make_ops(kind, base, rep, args.n_ops, w4_params)
            pool = request_pool(base, ops, rep, args.pool)
            t0 = time.perf_counter()
            D = reference_states(base, ops, pool)
            t_ref = time.perf_counter() - t0
            seed = rep + 1

            tot, mon_ms, a_err, a_final = run_sequential(base, ops, args.theta, seed, pool, D)
            b = run_concurrent(base, ops, args.theta, seed, pool, D, cores, args.n_rest)

            rest_us, during_us = b["rest_ns"] / 1000.0, b["during_ns"] / 1000.0
            p95_rest, p95_during = pct(rest_us, 95), pct(during_us, 95)
            p95_rest_end = pct(b["rest_end_ns"] / 1000.0, 95)
            p95_rest_worst = max(p95_rest, p95_rest_end)
            row = {
                "carga": carga, "n": args.n, "rep": rep, "op": kind, "n_ops": len(ops),
                "op_p50_ms": pct(tot, 50), "op_p95_ms": pct(tot, 95), "op_mean_ms": round(float(tot.mean()), 4),
                "monitor_p50_ms": pct(mon_ms, 50), "evaluador_p50_ms": pct(tot - mon_ms, 50),
                "op_concurrente_p50_ms": pct(b["op_ms"], 50), "op_concurrente_p95_ms": pct(b["op_ms"], 95),
                "lectura_reposo_p50_us": pct(rest_us, 50), "lectura_reposo_p95_us": p95_rest,
                "lectura_reposo_final_p95_us": p95_rest_end,
                "lectura_cambios_p50_us": pct(during_us, 50), "lectura_cambios_p95_us": p95_during,
                # razon contra el reposo inicial (la del plan) y contra el peor de los dos reposos
                "razon_p95": round(p95_during / p95_rest, 3) if p95_rest and p95_during != "" else "",
                "razon_p95_corregida": round(p95_during / p95_rest_worst, 3) if p95_rest_worst and p95_during != "" else "",
                "deriva_estado": round(p95_rest_end / p95_rest, 3) if p95_rest else "",
                "lecturas": b["n_reads"], "lecturas_traslapadas": b["n_reads_overlap"],
                "inconsistentes": b["inconsistent"],
                "errores_lector": sum(b["reader_errors"].values()),
                "errores_escritor": sum(b["writer_errors"].values()) + sum(a_err.values()),
                "final_ok_secuencial_pct": round(a_final, 3), "final_ok_concurrente_pct": round(b["final_ok_pct"], 3),
            }
            if kind == "toggle":  # costo de cada mitad por separado
                h = len(ops) // 2
                row["desactivar_p50_ms"], row["activar_p50_ms"] = pct(tot[:h], 50), pct(tot[h:], 50)
            rows.append(row)
            details.append({"rep": rep, "op": kind, "errores_lector": b["reader_errors"],
                            "errores_escritor": {**b["writer_errors"], **{f"secuencial:{k}": v for k, v in a_err.items()}},
                            "ejemplos_inconsistentes": b["examples"], "t_referencia_s": round(t_ref, 2)})
            log(f"rep {rep + 1}/{args.reps} {kind}: op p50={row['op_p50_ms']} ms p95={row['op_p95_ms']} ms | "
                f"lectura p95 reposo={p95_rest} us, reposo final={p95_rest_end} us, con cambios={p95_during} us | "
                f"{row['lecturas_traslapadas']} lecturas traslapadas, inconsistentes={row['inconsistentes']}, "
                f"errores lector={row['errores_lector']} escritor={row['errores_escritor']}")

    with open(out_dir / f"{tag}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in rows for k in r)), restval="")
        w.writeheader()
        w.writerows(rows)

    resumen = {}
    for kind in args.ops:
        r = [x for x in rows if x["op"] == kind]
        resumen[kind] = {
            "op_p50_ms": st.median(x["op_p50_ms"] for x in r), "op_p95_ms": st.median(x["op_p95_ms"] for x in r),
            "monitor_p50_ms": st.median(x["monitor_p50_ms"] for x in r),
            "evaluador_p50_ms": st.median(x["evaluador_p50_ms"] for x in r),
            "lectura_reposo_p95_us": st.median(x["lectura_reposo_p95_us"] for x in r),
            "lectura_reposo_final_p95_us": st.median(x["lectura_reposo_final_p95_us"] for x in r),
            "deriva_estado": st.median(x["deriva_estado"] for x in r),
            "razon_p95_corregida": st.median(x["razon_p95_corregida"] for x in r),
            "lectura_cambios_p95_us": st.median(x["lectura_cambios_p95_us"] for x in r),
            "razon_p95": st.median(x["razon_p95"] for x in r),
            "lecturas_traslapadas": sum(x["lecturas_traslapadas"] for x in r),
            "inconsistentes": sum(x["inconsistentes"] for x in r),
            "errores_lector": sum(x["errores_lector"] for x in r),
            "errores_escritor": sum(x["errores_escritor"] for x in r),
            "final_ok_min_pct": min(min(x["final_ok_secuencial_pct"], x["final_ok_concurrente_pct"]) for x in r),
        }
        if kind == "toggle":
            for k in ("desactivar_p50_ms", "activar_p50_ms"):
                resumen[kind][k] = st.median(x[k] for x in r)
    meta = {
        "experimento": "E5", "carga": carga, "n_eventos": args.n, "repeticiones": args.reps,
        "n_ops": args.n_ops, "theta": args.theta, "pool": args.pool, "n_reposo": args.n_rest,
        "semilla_datos": args.data_seed, "parametros_w4": w4_params,
        "nucleos_escritor_lector": cores, "gil_switch_interval_s": sys.getswitchinterval(),
        "resumen": resumen, "detalle": details,
        "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "nodo": socket.gethostname(), "cpu": run_e1.cpu_model(),
        "python": sys.version.split()[0], "dependencias": run_e1.versions(),
        "commit_demo": os.environ.get("DEVAC_COMMIT", "desconocido"),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "slurm_array_job_id": os.environ.get("SLURM_ARRAY_JOB_ID"),
        "slurm_array_task_id": os.environ.get("SLURM_ARRAY_TASK_ID"),
    }
    with open(out_dir / f"{tag}_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    log("resumen: " + json.dumps(resumen, ensure_ascii=False))


if __name__ == "__main__":
    main()
