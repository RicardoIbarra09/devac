#!/usr/bin/env python3
"""
E3: costo de construir la estructura de DEvAC (grafo -> comunidades -> indices).

Para un tamano n y una variante de W4, la carga de politicas es fija (semilla de datos)
y cada repeticion cambia solo la semilla de Louvain. Por repeticion se mide:
  1. Construccion del grafo con indice auxiliar (GraphBuilder.build_event_graph),
     desglosada por etapa con last_stats: pesos, nodos, cubetas, pares.
  2. Louvain (CommunityDetector.detect_communities) y su modularidad Q.
  3. Asignacion de politicas a comunidades.
  4. Indices del evaluador (CommunityPolicyEvaluator.run).
  5. Variante sin indice (solo si n <= --no-index-max): compara todos los pares con la
     misma similitud y el mismo umbral, y verifica que el grafo sea identico.
Al final hace una pasada aparte con tracemalloc para la RAM pico (tracemalloc hace
lenta la construccion, por eso no se mezcla con los tiempos) y guarda la particion
de la repeticion 0 para reutilizarla en E4-E6.

Uso:
  python run_e3.py --n 2000 --reps 30 --out-dir resultados/e3                       # W4-U
  python run_e3.py --n 2000 --mode estructurada --p-mix 0.1 --out-dir resultados/e3 # W4-E0.1
"""
import argparse
import copy
import csv
import gc
import json
import os
import random
import resource
import socket
import statistics as st
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "E2"))
sys.path.insert(0, str(HERE.parent / "E1"))

import run_e1  # noqa: E402
from gen_w4 import DEFAULTS as W4_DEFAULTS, MODES as W4_MODES, generate_w4, variant_label  # noqa: E402
from devac import CommunityDetector, CommunityPolicyEvaluator, GraphBuilder, Policy  # noqa: E402


def naive_graph(gb, policies, threshold):
    """Grafo comparando TODOS los pares (sin cubetas ni atributos omitibles)."""
    weights = GraphBuilder.calculate_attribute_weights(policies)
    events = [e for p in policies for e in p.events]
    G = nx.Graph()
    G.add_nodes_from(e.event_id for e in events)
    t0 = time.perf_counter()
    sim = gb.event_similarity
    for i in range(len(events)):
        e1 = events[i]
        for j in range(i + 1, len(events)):
            s = sim(e1, events[j], weights)
            if s >= threshold:
                G.add_edge(e1.event_id, events[j].event_id, weight=float(s))
    return G, time.perf_counter() - t0


def same_graph(a, b):
    if set(a.nodes) != set(b.nodes) or a.number_of_edges() != b.number_of_edges():
        return False
    return all(b.has_edge(u, v) and abs(b[u][v]["weight"] - d["weight"]) < 1e-12
               for u, v, d in a.edges(data=True))


def build_once(policies, threshold, louvain_seed):
    """Construye toda la estructura; devuelve (grafo, stats, pbc, event_to_community)."""
    gb = GraphBuilder()
    G = gb.build_event_graph(policies, similarity_threshold=threshold, export=False)
    stats = dict(gb.last_stats)

    random.seed(louvain_seed)  # Louvain usa el random global del proceso
    det = CommunityDetector()
    t0 = time.perf_counter()
    _, ev2c = det.detect_communities(G)
    stats["t_louvain_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    ev2p = {e.event_id: p.policy_id for p in policies for e in p.events}
    pbc = det.map_policies_to_communities(ev2p, ev2c)
    stats["t_map_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    ev = CommunityPolicyEvaluator()
    ev.run(policies, pbc)
    stats["t_indices_s"] = time.perf_counter() - t0
    return gb, G, stats, pbc, ev2c


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--reps", type=int, default=30)
    ap.add_argument("--data-seed", type=int, default=1, help="Semilla de la carga W4 (fija entre repeticiones)")
    ap.add_argument("--mode", choices=W4_MODES, default="uniforme")
    ap.add_argument("--p-mix", type=float, default=W4_DEFAULTS["p_mix"])
    ap.add_argument("--theta", type=float, default=0.3, help="Umbral de similitud para crear aristas")
    ap.add_argument("--no-index-max", type=int, default=5000, help="Mayor n para la variante sin indice")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    w4_params = {**W4_DEFAULTS, "mode": args.mode, "p_mix": args.p_mix}
    variant = variant_label(w4_params)
    tag = f"e3_{variant}_n{args.n}"
    log = lambda m: print(f"[E3 W4-{variant} n={args.n}] {m}", flush=True)

    raw = generate_w4(args.n, seed=args.data_seed, **w4_params)
    base = [Policy.model_validate(p) for p in raw]
    do_naive = args.n <= args.no_index_max

    rows = []
    for rep in range(args.reps):
        pols = copy.deepcopy(base)
        gc.collect()
        gb, G, s, pbc, ev2c = build_once(pols, args.theta, louvain_seed=rep + 1)

        comms = {}
        for node, c in ev2c.items():
            comms.setdefault(c, set()).add(node)
        q = nx.community.modularity(G, comms.values(), weight="weight") if G.number_of_edges() else 0.0
        sizes = sorted((len(v) for v in pbc.values()), reverse=True)

        t_graph = s["t_total_s"]
        row = {
            "variante": variant, "n": args.n, "rep": rep, "louvain_seed": rep + 1, "theta": args.theta,
            "t_weights_s": round(s["t_weights_s"], 5), "t_nodes_s": round(s["t_nodes_s"], 5),
            "t_index_s": round(s["t_index_s"], 5), "t_pairs_s": round(s["t_pairs_s"], 5),
            "t_graph_s": round(t_graph, 5), "t_louvain_s": round(s["t_louvain_s"], 5),
            "t_map_s": round(s["t_map_s"], 5), "t_indices_s": round(s["t_indices_s"], 5),
            "t_total_s": round(t_graph + s["t_louvain_s"] + s["t_map_s"] + s["t_indices_s"], 5),
            "nodes": s["nodes"], "edges": s["edges"],
            "density": round(nx.density(G), 6),
            "pairs_compared": s["pairs_compared"], "pairs_total": s["pairs_total"],
            "pairs_frac_pct": round(100.0 * s["pairs_compared"] / s["pairs_total"], 3) if s["pairs_total"] else "",
            "buckets": s["buckets"], "max_bucket": s["max_bucket"],
            "omitibles": "|".join(s["omitibles"]),
            "n_communities": len(pbc), "community_max": sizes[0] if sizes else 0,
            "modularity_q": round(q, 5),
            "naive_t_pairs_s": "", "naive_identical": "",
        }
        if do_naive:
            Gn, t_naive = naive_graph(gb, pols, args.theta)
            row["naive_t_pairs_s"] = round(t_naive, 5)
            row["naive_identical"] = same_graph(G, Gn)
            del Gn
        rows.append(row)

        if rep == 0:
            part = {"variante": variant, "n": args.n, "data_seed": args.data_seed, "louvain_seed": 1,
                    "theta": args.theta,
                    "policies_by_community": {str(k): v for k, v in pbc.items()},
                    "event_to_community": {k: int(v) for k, v in ev2c.items()}}
            with open(out_dir / f"{tag}_particion.json", "w", encoding="utf-8") as f:
                json.dump(part, f)
        if rep == 0 or (rep + 1) % 10 == 0 or rep + 1 == args.reps:
            extra = f", sin indice {row['naive_t_pairs_s']} s (identico={row['naive_identical']})" if do_naive else ""
            log(f"rep {rep + 1}/{args.reps}: total {row['t_total_s']} s (grafo {row['t_graph_s']}, "
                f"louvain {row['t_louvain_s']}), {row['edges']} aristas, {row['n_communities']} comunidades, "
                f"Q={row['modularity_q']}{extra}")
        del gb, G

    # Pasada de memoria (separada de los tiempos)
    pols = copy.deepcopy(base)
    gc.collect()
    tracemalloc.start()
    _gb, _G, _s, _pbc, _ = build_once(pols, args.theta, louvain_seed=1)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del _gb, _G, _pbc
    mem_peak_mb = peak / 2**20

    csv_path = out_dir / f"{tag}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    med = lambda k: st.median(r[k] for r in rows)
    qs = [r["modularity_q"] for r in rows]
    cs = [r["n_communities"] for r in rows]
    resumen = {
        "t_total_mediana_s": med("t_total_s"), "t_grafo_mediana_s": med("t_graph_s"),
        "t_pares_mediana_s": med("t_pairs_s"), "t_louvain_mediana_s": med("t_louvain_s"),
        "t_indices_mediana_s": med("t_indices_s"),
        "aristas": rows[0]["edges"], "densidad": rows[0]["density"],
        "pares_comparados": rows[0]["pairs_compared"], "pares_totales": rows[0]["pairs_total"],
        "pares_comparados_pct": rows[0]["pairs_frac_pct"],
        "q_mediana": st.median(qs), "q_desv": round(st.pstdev(qs), 5),
        "comunidades_mediana": st.median(cs), "comunidades_min": min(cs), "comunidades_max": max(cs),
        "ram_pico_construccion_mb": round(mem_peak_mb, 1),
        "rss_pico_proceso_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1),
        "sin_indice_t_pares_mediana_s": med("naive_t_pairs_s") if do_naive else None,
        "sin_indice_grafo_identico": all(r["naive_identical"] for r in rows) if do_naive else None,
    }
    meta = {
        "experimento": "E3", "carga": f"W4-{variant}", "variante": variant, "n_eventos": args.n,
        "semilla_datos": args.data_seed, "semillas_louvain": [r["louvain_seed"] for r in rows],
        "repeticiones": args.reps, "theta": args.theta, "parametros_w4": w4_params,
        "omitibles": rows[0]["omitibles"].split("|") if rows[0]["omitibles"] else [],
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
