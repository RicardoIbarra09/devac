# D1: exactitud del camino incremental, sin reconstrucciones de por medio.
#   - Q incremental contra Q completa de networkx en cada cambio
#   - grafo incremental contra un build completo con los pesos congelados, cada --check-graph-every
#     cambios y al final: nodos, aristas y pesos, y la Q de networkx sobre ese grafo reconstruido
#
# uso:
#   python3 paper1_dinamica/D1_exactitud/run_d1.py --policies experiments/politicas_un_evento.json \
#       --scenario mixed --changes 5000 --rep 1 --out-dir <carpeta>
#
# escribe <escenario>_<etiqueta>_rNN.json (filas por cambio), _rNN_meta.json y _rNN.csv (una fila
# con los errores maximos; resumen_d1.py junta todos los csv)

import copy
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import json  # noqa: E402

import run_dynamics as rd  # noqa: E402
from devac.graph import GraphBuilder  # noqa: E402
from entorno import TIMING_NOT_REPORTED, environment  # noqa: E402

CSV_COLUMNS = [
    "dataset", "scenario", "rep", "plan_seed", "louvain_seed", "changes", "rebuilds",
    "networkx_checks", "max_abs_q_incremental_vs_networkx",
    "graph_checks", "node_mismatches", "edge_mismatches", "max_abs_weight_diff", "max_abs_q_vs_rebuilt_graph",
    "excluded_initial", "initial_nodes", "initial_edges", "final_nodes", "final_edges",
]


def _snapshot(graph):
    nodes = {n: d for n, d in graph.nodes(data=True)}
    edges = {tuple(sorted((u, v))): d["weight"] for u, v, d in graph.edges(data=True)}
    return nodes, edges


# el grafo que daria un build completo hoy: politicas vigentes sin los excluidos, pesos congelados
def compare_graph(change: int, row: dict, monitor) -> dict:
    evaluator, builder = monitor.evaluator, monitor.graph_builder
    used = [p.model_copy(update={"events": [e for e in p.events if e.event_id not in evaluator.excluded]})
            for p in copy.deepcopy(list(evaluator.policies.values()))]
    ref = GraphBuilder()
    ref.build_event_graph(used, similarity_threshold=builder.similarity_threshold, export=False,
                          weights=builder.weights)
    inc_nodes, inc_edges = _snapshot(builder.graph)
    ref_nodes, ref_edges = _snapshot(ref.graph)
    common = inc_edges.keys() & ref_edges.keys()
    q_ref = monitor._compute_modularity(ref.graph)
    return {
        "change": change,
        "nodes": len(ref_nodes),
        "edges": len(ref_edges),
        "node_mismatches": len(inc_nodes.keys() ^ ref_nodes.keys())
                           + sum(inc_nodes[n] != ref_nodes[n] for n in inc_nodes.keys() & ref_nodes.keys()),
        "edge_mismatches": len(inc_edges.keys() ^ ref_edges.keys()),
        "max_abs_weight_diff": max((abs(inc_edges[e] - ref_edges[e]) for e in common), default=0.0),
        "abs_q_vs_rebuilt_graph": abs(row["q_incremental"] - q_ref),
    }


def main(argv=None) -> int:
    ap = rd.parser()
    ap.description = "D1: Q incremental vs networkx y grafo incremental vs build completo"
    ap.set_defaults(stress_threshold=1e9, tag="norebuild")
    ap.add_argument("--check-graph-every", type=int, default=50,
                    help="cada cuantos cambios se compara contra un build completo (y siempre al final)")
    args = ap.parse_args(argv)
    rd.check_args(ap, args)
    if args.rep is None:
        ap.error("D1 corre una repeticion por tarea: falta --rep")
    if args.check_graph_every < 1:
        ap.error("--check-graph-every debe ser al menos 1")

    changes = args.changes or rd.DEFAULT_CHANGES[args.scenario]
    seed = rd.rep_seed(args.seed, args.rep)
    out_dir = rd.ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    policies = rd.load_policies(args.policies)
    plan = rd.build_plan(policies, args.scenario, changes, seed)
    print(f"D1 {args.scenario}: {changes} cambios, grafo cada {args.check_graph_every}, r{args.rep:02d}", flush=True)

    started = datetime.now(timezone.utc)
    env = environment()
    checks = []

    def after(i, row, monitor):
        if i % args.check_graph_every == 0 or i == len(plan):
            checks.append(compare_graph(i, row, monitor))
            c = checks[-1]
            print(f"  cambio {i}: {c['nodes']} nodos, {c['edges']} aristas, nodos distintos {c['node_mismatches']}, "
                  f"aristas distintas {c['edge_mismatches']}, |dQ| {c['abs_q_vs_rebuilt_graph']:.1e}",
                  file=sys.__stdout__, flush=True)  # run_once redirige stdout

    run = rd.run_once(policies, plan, args, louvain_seed=seed, after_change=after)
    s = rd.summarize(run)
    name = f"{args.scenario}_{args.tag}_r{args.rep:02d}"
    (out_dir / f"{name}.json").write_text(json.dumps(run["rows"], ensure_ascii=False), encoding="utf-8")

    result = {
        "dataset": Path(args.policies).stem,
        "scenario": args.scenario,
        "rep": args.rep,
        "plan_seed": seed,
        "louvain_seed": seed,
        "changes": s["changes"],
        "rebuilds": s["rebuilds"],
        "networkx_checks": s["networkx_changes"],
        "max_abs_q_incremental_vs_networkx": s["max_abs_q_diff"],
        "graph_checks": len(checks),
        "node_mismatches": sum(c["node_mismatches"] for c in checks),
        "edge_mismatches": sum(c["edge_mismatches"] for c in checks),
        "max_abs_weight_diff": max(c["max_abs_weight_diff"] for c in checks),
        "max_abs_q_vs_rebuilt_graph": max(c["abs_q_vs_rebuilt_graph"] for c in checks),
        "excluded_initial": s["excluded_initial"],
        "initial_nodes": s["initial_nodes"],
        "initial_edges": s["initial_edges"],
        "final_nodes": s["final_nodes"],
        "final_edges": s["final_edges"],
    }
    with open(out_dir / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerow(result)

    meta = {
        "script": "paper1_dinamica/D1_exactitud/run_d1.py",
        "argv": sys.argv[1:] if argv is None else argv,
        "params": {**vars(args), "changes": changes},
        **rd.base_meta(args, started, env),
        "plan_seeds": [seed],
        "louvain_seeds": [seed],
        "result": result,
        "graph_checks": checks,
        "files": [f"{name}.json"],
        "replications": [{"rep": args.rep, "plan_seed": seed, "plan_counts": rd.plan_counts(plan), **s}],
    }
    if meta["timing"] == TIMING_NOT_REPORTED:
        meta["timing_note"] = "corrida sin TIMING=1: las columnas de tiempo se guardan pero no van al paper"
    (out_dir / f"{name}_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"D1 r{args.rep:02d}: |Q inc - Q networkx| max {result['max_abs_q_incremental_vs_networkx']:.1e}, "
          f"aristas distintas {result['edge_mismatches']}, |dQ| contra grafo reconstruido max "
          f"{result['max_abs_q_vs_rebuilt_graph']:.1e} -> {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
