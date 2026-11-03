#!/usr/bin/env python3
"""
E4: sensibilidad de DEvAC al umbral de similitud θ (el que decide si dos politicas se conectan).

Para un θ y una carga (W4 de n eventos, o un JSON con --policies):
  - la carga, las solicitudes y el oraculo son los mismos para todos los θ (comparacion pareada)
  - por repeticion cambia la semilla de Louvain; se construye la estructura con ese θ y se mide:
    conexiones, comunidades, Q, comunidad mayor, politicas aisladas, tiempo de construccion,
    latencia p50/p95/p99 y pasos de DEvAC y del lineal (protocolo de E2) y concordancia
  - el lineal no depende de θ, pero se mide en cada repeticion para que el speedup sea pareado

Uso:
  python run_e4.py --theta 0.3 --n 10000 --reps 10 --out-dir resultados/e4                        # W4-U
  python run_e4.py --theta 0.3 --n 10000 --mode estructurada --p-mix 0.1 --out-dir resultados/e4  # W4-E0.1
  python run_e4.py --theta 0.3 --policies experiments/w3.json --label W3 --out-dir resultados/e4  # otra carga
"""
import argparse
import copy
import json
import csv
import os
import random
import resource
import socket
import statistics as st
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "E2"))
sys.path.insert(0, str(HERE.parent / "E1"))

import run_e1  # noqa: E402  (al importarlo desactiva la exportacion gexf)
from run_e2 import measure  # noqa: E402
from gen_w4 import DEFAULTS as W4_DEFAULTS, MODES as W4_MODES, generate_w4, variant_label  # noqa: E402
from devac import (  # noqa: E402
    CommunityDetector, CommunityPolicyEvaluator, GraphBuilder, Policy, PolicyLoader, TraditionalPolicyEvaluator,
)


def pct(a, q):
    return round(float(np.percentile(a, q)), 3)


# grafo con el umbral θ + louvain + indices
def build(policies, theta, louvain_seed):
    t0 = time.perf_counter()
    gb = GraphBuilder()
    G = gb.build_event_graph(policies, similarity_threshold=theta, export=False)
    random.seed(louvain_seed)  # louvain usa el random global
    det = CommunityDetector()
    _, ev2c = det.detect_communities(G)
    ev2p = {e.event_id: p.policy_id for p in policies for e in p.events}
    pbc = det.map_policies_to_communities(ev2p, ev2c)
    ev = CommunityPolicyEvaluator()
    ev.run(policies, pbc)
    build_s = time.perf_counter() - t0

    comms = {}
    for node, c in ev2c.items():
        comms.setdefault(c, set()).add(node)
    q = nx.community.modularity(G, comms.values(), weight="weight") if G.number_of_edges() else 0.0
    sizes = sorted((len(v) for v in pbc.values()), reverse=True)
    info = {
        "edges": G.number_of_edges(), "density": round(nx.density(G), 6),
        "isolated": sum(1 for _ in nx.isolates(G)),
        "n_communities": len(pbc), "community_max": sizes[0] if sizes else 0,
        "community_median": sizes[len(sizes) // 2] if sizes else 0,
        "modularity_q": round(q, 5), "build_s": round(build_s, 4),
        "t_graph_s": round(gb.last_stats["t_total_s"], 4),
    }
    return ev, info


def main():
    ap = argparse.ArgumentParser(description="E4: sensibilidad al umbral de similitud θ")
    ap.add_argument("--theta", type=float, required=True)
    ap.add_argument("--n", type=int, default=10000)
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--data-seed", type=int, default=1)
    ap.add_argument("--mode", choices=W4_MODES, default="uniforme")
    ap.add_argument("--p-mix", type=float, default=W4_DEFAULTS["p_mix"])
    ap.add_argument("--policies", help="JSON de politicas en vez de W4 (p. ej. W3)")
    ap.add_argument("--label", help="nombre de la carga cuando se usa --policies")
    ap.add_argument("--n-warmup", type=int, default=1000)
    ap.add_argument("--n-measure", type=int, default=2000)
    ap.add_argument("--no-oracle", action="store_true")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # carga
    if args.policies:
        base = PolicyLoader.from_file(args.policies)
        carga = args.label or Path(args.policies).stem
        w4_params = None
    else:
        w4_params = {**W4_DEFAULTS, "mode": args.mode, "p_mix": args.p_mix}
        carga = f"W4-{variant_label(w4_params)}"
        base = [Policy.model_validate(p) for p in generate_w4(args.n, seed=args.data_seed, **w4_params)]
    n_events = sum(len(p.events) for p in base)
    tag = f"e4_{carga}_n{n_events}_theta{args.theta:g}"
    log = lambda m: print(f"[E4 {carga} n={n_events} θ={args.theta:g}] {m}", flush=True)

    # solicitudes y oraculo: no dependen de θ
    compiled = run_e1.compile_policies(base)
    values, failed = run_e1.generate_requests(base, compiled, args.n_warmup + args.n_measure, args.data_seed)
    warm_vals, meas_vals = values[: args.n_warmup], values[args.n_warmup:]
    truth = None if args.no_oracle else [run_e1.oracle_decision(compiled, v)[0] for v in meas_vals]
    log(f"{len(meas_vals)} solicitudes medidas (fallos de generacion: {failed})")

    linear = TraditionalPolicyEvaluator(copy.deepcopy(base))
    lin_warm = [run_e1.to_access_request(v) for v in warm_vals]
    lin_meas = [run_e1.to_access_request(v) for v in meas_vals]
    for r in lin_warm:
        linear.evaluate(r)

    rows = []
    for rep in range(args.reps):
        pols = copy.deepcopy(base)
        devac, info = build(pols, args.theta, louvain_seed=rep + 1)
        dev_warm = [run_e1.to_access_request(v) for v in warm_vals]
        dev_meas = [run_e1.to_access_request(v) for v in meas_vals]
        for r in dev_warm:
            devac.evaluate(r)

        # se alterna el orden para no favorecer a ninguno
        res = {}
        for name in (["devac", "lineal"] if rep % 2 == 0 else ["lineal", "devac"]):
            ev, reqs = (devac, dev_meas) if name == "devac" else (linear, lin_meas)
            res[name] = measure(ev, reqs)

        lat_d, steps_d, dec_d = res["devac"]
        lat_l, steps_l, dec_l = res["lineal"]
        lat_d, lat_l = lat_d / 1000.0, lat_l / 1000.0
        sd = steps_d[steps_d >= 0]
        sl = steps_l[steps_l >= 0]
        row = {
            "carga": carga, "n": n_events, "theta": args.theta, "rep": rep, "louvain_seed": rep + 1, **info,
            "devac_p50_us": pct(lat_d, 50), "devac_p95_us": pct(lat_d, 95), "devac_p99_us": pct(lat_d, 99),
            "lineal_p50_us": pct(lat_l, 50), "lineal_p95_us": pct(lat_l, 95), "lineal_p99_us": pct(lat_l, 99),
            "speedup_mean": round(float(lat_l.mean() / lat_d.mean()), 3),
            "devac_steps_mean": round(float(sd.mean()), 2) if len(sd) else "",
            "devac_steps_p99": pct(sd, 99) if len(sd) else "",
            "lineal_steps_mean": round(float(sl.mean()), 2) if len(sl) else "",
            "early_deny_pct": round(100.0 * float((sd == 0).mean()), 2) if len(sd) else "",
            "conc_lineal_pct": round(100.0 * sum(a == b for a, b in zip(dec_d, dec_l)) / len(dec_d), 4),
            "conc_oraculo_pct": round(100.0 * sum(a == b for a, b in zip(dec_d, truth)) / len(dec_d), 4) if truth else "",
            "fp": sum(a == "allow" and t == "deny" for a, t in zip(dec_d, truth)) if truth else "",
        }
        rows.append(row)
        log(f"rep {rep + 1}/{args.reps}: {info['edges']} conexiones, {info['n_communities']} comunidades "
            f"(mayor {info['community_max']}), Q={info['modularity_q']}, construccion {info['build_s']} s, "
            f"p95 devac={row['devac_p95_us']} us, lineal={row['lineal_p95_us']} us, conc={row['conc_lineal_pct']}%")
        del devac, pols

    with open(out_dir / f"{tag}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    med = lambda k: st.median(r[k] for r in rows)
    resumen = {
        "conexiones": rows[0]["edges"], "aislados": rows[0]["isolated"],
        "comunidades_mediana": med("n_communities"),
        "comunidades_min": min(r["n_communities"] for r in rows), "comunidades_max": max(r["n_communities"] for r in rows),
        "comunidad_mayor_mediana": med("community_max"), "q_mediana": med("modularity_q"),
        "construccion_mediana_s": med("build_s"),
        "devac_p50_us": med("devac_p50_us"), "devac_p95_us": med("devac_p95_us"), "devac_p99_us": med("devac_p99_us"),
        "lineal_p95_us": med("lineal_p95_us"), "speedup_mean_mediana": med("speedup_mean"),
        "pasos_media_mediana": med("devac_steps_mean") if rows[0]["devac_steps_mean"] != "" else None,
        "deny_temprano_pct": med("early_deny_pct") if rows[0]["early_deny_pct"] != "" else None,
        "concordancia_lineal_min_pct": min(r["conc_lineal_pct"] for r in rows),
        "concordancia_oraculo_min_pct": min(r["conc_oraculo_pct"] for r in rows) if truth else None,
        "fp_total": sum(r["fp"] for r in rows) if truth else None,
        "rss_pico_proceso_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1),
    }
    meta = {
        "experimento": "E4", "carga": carga, "n_eventos": n_events, "theta": args.theta,
        "repeticiones": args.reps, "semilla_datos": args.data_seed,
        "semillas_louvain": [r["louvain_seed"] for r in rows],
        "n_calentamiento": args.n_warmup, "n_medidas": args.n_measure,
        "politicas": args.policies, "parametros_w4": w4_params, "resumen": resumen,
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


if __name__ == "__main__":
    main()
