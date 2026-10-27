# uso:
#   poetry run python experiments/run_dynamics.py --scenario deactivation --reps 10
#   poetry run python experiments/run_dynamics.py --scenario deactivation --reps 10 --stress-threshold 1e9
#   poetry run python experiments/run_dynamics.py --scenario mixed --changes 821 --reps 10
#
# cada repeticion aplica la misma secuencia de cambios (sale de --seed); entre repeticiones solo
# cambia la semilla de louvain. por cada corrida escribe <escenario>_<etiqueta>_rNN.json con una
# fila por cambio, y <escenario>_<etiqueta>_meta.json con parametros, fecha, commit y resumen

import argparse
import contextlib
import copy
import io
import json
import platform
import random
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import networkx as nx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from devac.communities import CommunityDetector  # noqa: E402
from devac.evaluator import CommunityPolicyEvaluator  # noqa: E402
from devac.graph import GraphBuilder  # noqa: E402
from devac.loader import PolicyLoader  # noqa: E402
from devac.models import Policy  # noqa: E402
from devac.monitor import ModularityMonitor  # noqa: E402

DIMS = ("subject", "asset", "space", "time", "action")
DEFAULT_CHANGES = {"deactivation": 1000, "mixed": 821}
MIXED_WEIGHTS = {"add": 0.25, "update": 0.25, "remove": 0.15, "deactivate": 0.2, "reactivate": 0.15}


class _QuietGraphBuilder(GraphBuilder):
    def export_graph(self, output_path: str, format: str = "gexf") -> None:
        pass  # el gexf de cada reconstruccion no hace falta aqui


# --- plan de cambios: depende solo de las politicas y de la semilla ---

def _with_value_from(policy: dict, donor: dict, rng: random.Random) -> None:
    for event in policy["events"]:
        dim = rng.choice(DIMS)
        event[dim]["value"] = copy.deepcopy(rng.choice(donor["events"])[dim]["value"])


def build_plan(policies: List[dict], scenario: str, changes: int, seed: int) -> List[dict]:
    rng = random.Random(f"{seed}-plan")
    active: Dict[str, dict] = {p["policy_id"]: p for p in copy.deepcopy(policies) if p["events"]}
    inactive: Dict[str, dict] = {}  # politica con sus eventos de antes de desactivarla
    plan = []

    if scenario == "deactivation":
        if changes > len(active):
            raise SystemExit(f"solo hay {len(active)} políticas activas para {changes} desactivaciones")
        for pid in rng.sample(sorted(active), changes):
            plan.append({"change": "deactivate", "policy_id": pid})
        return plan

    next_id = 1
    for _ in range(changes):
        kinds = [k for k in MIXED_WEIGHTS if k != "reactivate" or inactive]
        if len(active) <= 1:
            kinds = [k for k in kinds if k in ("add", "reactivate")]  # nunca se queda sin politicas
        kind = rng.choices(kinds, [MIXED_WEIGHTS[k] for k in kinds])[0]

        if kind == "add":
            policy = copy.deepcopy(active[rng.choice(sorted(active))])
            _with_value_from(policy, active[rng.choice(sorted(active))], rng)
            policy["policy_id"] = f"dyn-p{next_id:05d}"
            policy["description"] = "agregada por run_dynamics"
            for k, event in enumerate(policy["events"], start=1):
                event["event_id"] = f"dyn-p{next_id:05d}-e{k}"
            next_id += 1
            active[policy["policy_id"]] = policy
            plan.append({"change": kind, "policy": copy.deepcopy(policy)})
        elif kind == "update":
            pid = rng.choice(sorted(active))
            _with_value_from(active[pid], active[rng.choice(sorted(active))], rng)
            plan.append({"change": kind, "policy": copy.deepcopy(active[pid])})
        elif kind == "remove":
            pid = rng.choice(sorted(active))
            del active[pid]
            plan.append({"change": kind, "policy_id": pid})
        elif kind == "deactivate":
            pid = rng.choice(sorted(active))
            inactive[pid] = active.pop(pid)
            plan.append({"change": kind, "policy_id": pid})
        else:
            pid = rng.choice(sorted(inactive))
            active[pid] = inactive.pop(pid)
            plan.append({"change": kind, "policy": copy.deepcopy(active[pid])})
    return plan


def apply_change(evaluator: CommunityPolicyEvaluator, change: dict) -> None:
    kind = change["change"]
    if kind == "add":
        evaluator.add_policy(Policy.model_validate(change["policy"]))
    elif kind in ("update", "reactivate"):
        evaluator.update_policy(Policy.model_validate(change["policy"]))
    elif kind == "remove":
        evaluator.remove_policy(change["policy_id"])
    else:
        # desactivar = dejarla sin eventos; para el grafo y Q es lo mismo que borrarla
        current = evaluator.policies[change["policy_id"]]
        evaluator.update_policy(current.model_copy(update={"events": []}))


# --- una corrida ---

def run_once(policies: List[dict], plan: List[dict], args, louvain_seed: int) -> dict:
    loaded = [Policy.model_validate(p) for p in copy.deepcopy(policies)]
    builder = _QuietGraphBuilder()
    evaluator = CommunityPolicyEvaluator([], {})
    evaluator.run(loaded, {})
    monitor = ModularityMonitor(
        evaluator, builder, CommunityDetector(seed=louvain_seed),
        threshold=args.stress_threshold, similarity_threshold=args.similarity_threshold,
        audit_policies=not args.no_audit, verify_modularity=True,
    )

    rebuild_times, rebuild_phases = [], []

    def timed_rebuild() -> bool:
        t0 = time.perf_counter()
        done = monitor.rebuild_now()
        rebuild_times.append(time.perf_counter() - t0)
        rebuild_phases.append(dict(monitor.last_rebuild_stats))
        return done

    monitor._trigger_rebuild = timed_rebuild  # en el mismo hilo, si no la corrida no se repite igual

    rows = []
    with contextlib.redirect_stdout(io.StringIO()):  # el evaluador imprime cada cambio
        timed_rebuild()  # build inicial
        for i, change in enumerate(plan, start=1):
            before = len(monitor.history)
            apply_change(evaluator, change)
            if len(monitor.history) != before + 1:
                raise RuntimeError(f"el cambio {i} ({change['change']}) no dejó exactamente una fila")
            row = dict(monitor.history[-1])
            row["change"] = change["change"]
            row["rebuilds"] = monitor.get_stats()["rebuilds"] - 1  # sin contar el build inicial
            rows.append(row)

    graph = builder.graph
    return {
        "rows": rows,
        "louvain_seed": louvain_seed,
        "initial_build_s": rebuild_times[0],
        "rebuild_s": rebuild_times[1:],
        "rebuild_phases": rebuild_phases,  # el primero es el build inicial
        "final_nodes": graph.number_of_nodes(),
        "final_edges": graph.number_of_edges(),
        "final_communities": len(evaluator.policies_by_community),
    }


def _mean(values: List[float]) -> Optional[float]:
    return statistics.fmean(values) if values else None


def summarize(run: dict) -> dict:
    rows = run["rows"]
    return {
        "louvain_seed": run["louvain_seed"],
        "changes": len(rows),
        "rebuilds": rows[-1]["rebuilds"] if rows else 0,
        "max_stress_q": max((r["stress_q"] for r in rows), default=0.0),
        "max_abs_q_diff": max((abs(r["q_incremental"] - r["q_networkx"]) for r in rows), default=0.0),
        "mean_t_networkx_s": _mean([r["t_networkx_s"] for r in rows]),
        "mean_t_incremental_s": _mean([r["t_incremental_s"] for r in rows]),
        "mean_t_proxy_s": _mean([r["t_proxy_s"] for r in rows]),
        "mean_t_graph_s": _mean([r["t_graph_s"] for r in rows]),
        "initial_build_s": run["initial_build_s"],
        "mean_rebuild_s": _mean(run["rebuild_s"]),
        # segunda pasada: grafo sin excluidos + louvain sobre el; incluye el build inicial
        "mean_second_pass_s": _mean([ph["t_build_used_s"] + ph["t_louvain_used_s"] for ph in run["rebuild_phases"]]),
        "mean_rebuild_phases_s": {
            key: _mean([ph[key] for ph in run["rebuild_phases"]])
            for key in ("t_build_s", "t_louvain_s", "t_audit_s", "t_build_used_s", "t_louvain_used_s", "t_index_s", "t_q_s")
        },
        "excluded_initial": run["rebuild_phases"][0]["excluded"],
        "final_nodes": run["final_nodes"],
        "final_edges": run["final_edges"],
        "final_communities": run["final_communities"],
    }


def _git(*cmd: str) -> Optional[str]:
    try:
        return subprocess.run(["git", *cmd], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Q completa vs incremental vs proxy ante cambios de políticas")
    parser.add_argument("--policies", default="experiments/politicas_un_evento.json")
    parser.add_argument("--scenario", choices=sorted(DEFAULT_CHANGES), required=True,
                        help="deactivation = desactivaciones (events=[]); mixed = altas, cambios, bajas, "
                             "desactivaciones y reactivaciones")
    parser.add_argument("--changes", type=int, help="por defecto 1000 (deactivation) u 821 (mixed)")
    parser.add_argument("--stress-threshold", type=float, default=0.05,
                        help="un valor muy alto (p. ej. 1e9) da la corrida sin reconstrucciones")
    parser.add_argument("--similarity-threshold", type=float, default=0.3)
    parser.add_argument("--no-audit", action="store_true")
    parser.add_argument("--seed", type=int, default=1, help="fija la secuencia de cambios")
    parser.add_argument("--reps", type=int, default=10, help="repeticiones; cambia la semilla de louvain")
    parser.add_argument("--tag", help="por defecto rebuild o norebuild según el umbral")
    parser.add_argument("--out-dir", default="experiments/resultados_dinamica")
    args = parser.parse_args(argv)

    if args.reps < 1:
        parser.error("--reps debe ser al menos 1")
    changes = args.changes or DEFAULT_CHANGES[args.scenario]
    tag = args.tag or ("norebuild" if args.stress_threshold >= 1e6 else "rebuild")
    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    policies = [p.model_dump() for p in PolicyLoader.from_file(str(ROOT / args.policies))]
    plan = build_plan(policies, args.scenario, changes, args.seed)
    counts = {k: sum(1 for c in plan if c["change"] == k) for k in sorted({c["change"] for c in plan})}
    # flush: con la salida redirigida a un log, sin esto no se ve el avance hasta el final
    print(f"{args.scenario}: {len(plan)} cambios {counts}, umbral {args.stress_threshold}, {args.reps} repeticiones", flush=True)

    started = datetime.now(timezone.utc)
    # se lee al arrancar: el commit con el que corre el codigo. los archivos sin seguimiento no cuentan
    # (otras corridas escriben resultados mientras tanto)
    commit = _git("rev-parse", "HEAD")
    dirty = bool(_git("status", "--porcelain", "--untracked-files=no"))
    summaries, files = [], []
    for rep in range(1, args.reps + 1):
        run = run_once(policies, plan, args, louvain_seed=args.seed * 1000 + rep)
        path = out_dir / f"{args.scenario}_{tag}_r{rep:02d}.json"
        path.write_text(json.dumps(run["rows"], ensure_ascii=False), encoding="utf-8")
        s = summarize(run)
        summaries.append(s)
        files.append(path.name)
        print(f"  r{rep:02d}: {s['rebuilds']} reconstrucciones, estrés máx {s['max_stress_q']:.4f}, "
              f"Q completa {s['mean_t_networkx_s'] * 1e3:.2f} ms, incremental {s['mean_t_incremental_s'] * 1e6:.1f} us, "
              f"proxy {s['mean_t_proxy_s'] * 1e6:.1f} us, |dQ| máx {s['max_abs_q_diff']:.1e}", flush=True)

    meta = {
        "script": "experiments/run_dynamics.py",
        "argv": sys.argv[1:] if argv is None else argv,
        "params": {**vars(args), "changes": changes, "tag": tag,
                   "mixed_weights": MIXED_WEIGHTS if args.scenario == "mixed" else None},
        "plan_counts": counts,
        "started_utc": started.isoformat(timespec="seconds"),
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": commit,
        "dirty": dirty,  # habia cambios sin commit en archivos con seguimiento al arrancar
        "python": platform.python_version(),
        "networkx": nx.__version__,
        "machine": platform.platform(),
        "columns": {
            "q_incremental": "Q incremental (la que mueve el estrés); q_real es la misma",
            "q_networkx": "Q completa con networkx sobre el mismo grafo, solo para verificar",
            "t_networkx_s": "tiempo de la Q completa; t_real_s es el mismo",
            "t_incremental_s": "tiempo de actualizar L_c, D_c y m con los vecinos de los nodos que cambiaron",
            "t_proxy_s": "tiempo del proxy (suma de k_i/2m de los eventos de la política)",
            "t_graph_s": "tiempo de actualizar nodos y aristas; lo necesitan la Q completa y la incremental",
            "rebuilds": "reconstrucciones hechas hasta este cambio, inclusive",
            "change": "cambio aplicado: add, update, remove, deactivate o reactivate",
        },
        "files": files,
        "replications": summaries,
    }
    meta_path = out_dir / f"{args.scenario}_{tag}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Resultados en {out_dir}/ ({meta_path.name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
