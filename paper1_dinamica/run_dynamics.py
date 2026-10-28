# uso:
#   poetry run python paper1_dinamica/run_dynamics.py --scenario deactivation --reps 10
#   poetry run python paper1_dinamica/run_dynamics.py --scenario deactivation --reps 10 --stress-threshold 1e9
#   poetry run python paper1_dinamica/run_dynamics.py --scenario mixed --changes 821 --reps 10
#   python3 paper1_dinamica/run_dynamics.py --scenario mixed --rep 3   (una sola repeticion, p. ej. una tarea de slurm)
#
# la repeticion r usa la semilla seed*1000 + r para su secuencia de cambios y para louvain; las dos
# quedan en el meta.json. con y sin reconstrucciones, la misma r aplica los mismos cambios.
# por cada corrida escribe <escenario>_<etiqueta>_rNN.json con una fila por cambio, y
# <escenario>_<etiqueta>_meta.json (o _rNN_meta.json con --rep) con parametros, entorno y resumen

import argparse
import contextlib
import copy
import hashlib
import io
import json
import random
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

import networkx as nx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from devac.communities import CommunityDetector  # noqa: E402
from devac.evaluator import CommunityPolicyEvaluator  # noqa: E402
from devac.graph import GraphBuilder  # noqa: E402
from devac.loader import PolicyLoader  # noqa: E402
from devac.models import Policy  # noqa: E402
from devac.monitor import ModularityMonitor  # noqa: E402
from entorno import TIMING_NOT_REPORTED, environment  # noqa: E402

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


def rep_seed(seed: int, rep: int) -> int:
    return seed * 1000 + rep


# cambios (1..changes) en los que se calcula la Q completa: todos, ninguno (0) o n repartidos parejo
def networkx_changes(changes: int, sample: Optional[int]) -> Set[int]:
    if sample is None or sample >= changes:
        return set(range(1, changes + 1))
    return {1 + (k * changes) // sample for k in range(sample)}


# --- una corrida ---

AfterChange = Callable[[int, dict, ModularityMonitor], None]


def run_once(policies: List[dict], plan: List[dict], args, louvain_seed: int,
             after_change: Optional[AfterChange] = None) -> dict:
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
        rebuild_phases.append({**monitor.last_rebuild_stats, "nodes": builder.graph.number_of_nodes(),
                               "edges": builder.graph.number_of_edges()})
        return done

    monitor._trigger_rebuild = timed_rebuild  # en el mismo hilo, si no la corrida no se repite igual

    verify = networkx_changes(len(plan), getattr(args, "networkx_sample", None))
    rows = []
    with contextlib.redirect_stdout(io.StringIO()):  # el evaluador imprime cada cambio
        timed_rebuild()  # build inicial
        for i, change in enumerate(plan, start=1):
            monitor.verify_modularity = i in verify
            before = len(monitor.history)
            apply_change(evaluator, change)
            if len(monitor.history) != before + 1:
                raise RuntimeError(f"el cambio {i} ({change['change']}) no dejó exactamente una fila")
            row = dict(monitor.history[-1])
            row["change"] = change["change"]
            row["rebuilds"] = monitor.get_stats()["rebuilds"] - 1  # sin contar el build inicial
            row["nodes"] = builder.graph.number_of_nodes()
            row["edges"] = builder.graph.number_of_edges()
            rows.append(row)
            if after_change is not None:
                after_change(i, row, monitor)

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
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def summarize(run: dict) -> dict:
    rows = run["rows"]
    checked = [r for r in rows if r["q_networkx"] is not None]
    return {
        "louvain_seed": run["louvain_seed"],
        "changes": len(rows),
        "rebuilds": rows[-1]["rebuilds"] if rows else 0,
        "max_stress_q": max((r["stress_q"] for r in rows), default=0.0),
        "mean_q": _mean([r["q_incremental"] for r in rows]),
        "networkx_changes": len(checked),
        "max_abs_q_diff": max((abs(r["q_incremental"] - r["q_networkx"]) for r in checked), default=None),
        "mean_t_networkx_s": _mean([r["t_networkx_s"] for r in rows]),
        "mean_t_incremental_s": _mean([r["t_incremental_s"] for r in rows]),
        "mean_t_graph_s": _mean([r["t_graph_s"] for r in rows]),
        "initial_build_s": run["initial_build_s"],
        "mean_rebuild_s": _mean(run["rebuild_s"]),
        "total_rebuild_s": sum(run["rebuild_s"]),
        # costo de mantener la red entre reconstrucciones: grafo + Q incremental, sin la Q completa
        "total_update_s": sum(r["t_graph_s"] + r["t_incremental_s"] for r in rows),
        # tamano de los grafos reconstruidos (nodos + aristas), no depende de la maquina
        "rebuilt_size": sum(ph["nodes"] + ph["edges"] for ph in run["rebuild_phases"][1:]),
        # segunda pasada: grafo sin excluidos + louvain sobre el; incluye el build inicial
        "mean_second_pass_s": _mean([ph["t_build_used_s"] + ph["t_louvain_used_s"] for ph in run["rebuild_phases"]]),
        "mean_rebuild_phases_s": {
            key: _mean([ph[key] for ph in run["rebuild_phases"]])
            for key in ("t_build_s", "t_louvain_s", "t_audit_s", "t_build_used_s", "t_louvain_used_s", "t_index_s", "t_q_s")
        },
        "excluded_initial": run["rebuild_phases"][0]["excluded"],
        "initial_nodes": run["rebuild_phases"][0]["nodes"],
        "initial_edges": run["rebuild_phases"][0]["edges"],
        "final_nodes": run["final_nodes"],
        "final_edges": run["final_edges"],
        "final_communities": run["final_communities"],
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Q completa vs Q incremental ante cambios de políticas")
    ap.add_argument("--policies", default="experiments/politicas_un_evento.json")
    ap.add_argument("--scenario", choices=sorted(DEFAULT_CHANGES), required=True,
                    help="deactivation = desactivaciones (events=[]); mixed = altas, cambios, bajas, "
                         "desactivaciones y reactivaciones")
    ap.add_argument("--changes", type=int, help="por defecto 1000 (deactivation) u 821 (mixed)")
    ap.add_argument("--stress-threshold", type=float, default=0.05,
                    help="un valor muy alto (p. ej. 1e9) da la corrida sin reconstrucciones")
    ap.add_argument("--similarity-threshold", type=float, default=0.3)
    ap.add_argument("--no-audit", action="store_true")
    ap.add_argument("--seed", type=int, default=1,
                    help="la repeticion r usa seed*1000 + r para sus cambios y para louvain")
    ap.add_argument("--reps", type=int, default=10, help="repeticiones")
    ap.add_argument("--rep", type=int, help="corre solo esa repeticion (mismas semillas que en una corrida "
                                            "completa) y escribe su propio _rNN_meta.json")
    ap.add_argument("--networkx-sample", type=int,
                    help="calcula la Q completa solo en n cambios repartidos parejo (0 = en ninguno); "
                         "por defecto en todos")
    ap.add_argument("--tag", help="por defecto rebuild o norebuild según el umbral")
    ap.add_argument("--out-dir", default="paper1_dinamica/resultados/crudo/local")
    return ap


def check_args(ap: argparse.ArgumentParser, args) -> None:
    if args.reps < 1:
        ap.error("--reps debe ser al menos 1")
    if args.rep is not None and args.rep < 1:
        ap.error("--rep debe ser al menos 1")
    if args.networkx_sample is not None and args.networkx_sample < 0:
        ap.error("--networkx-sample no puede ser negativo")


def load_policies(path: str) -> List[dict]:
    return [p.model_dump() for p in PolicyLoader.from_file(str(ROOT / path))]


def plan_counts(plan: List[dict]) -> Dict[str, int]:
    return {k: sum(1 for c in plan if c["change"] == k) for k in sorted({c["change"] for c in plan})}


TIMING_COLUMNS = ["t_networkx_s", "t_real_s", "t_incremental_s", "t_graph_s"]


# lo comun a los meta.json de D1-D4: archivo de politicas, fechas y entorno
def base_meta(args, started: datetime, env: dict) -> dict:
    return {
        "policies_sha256": _sha256(ROOT / args.policies),
        "started_utc": started.isoformat(timespec="seconds"),
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **env,
        "networkx": nx.__version__,
        "timing_columns": TIMING_COLUMNS,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = parser()
    args = ap.parse_args(argv)
    check_args(ap, args)
    reps = [args.rep] if args.rep is not None else list(range(1, args.reps + 1))
    changes = args.changes or DEFAULT_CHANGES[args.scenario]
    tag = args.tag or ("norebuild" if args.stress_threshold >= 1e6 else "rebuild")
    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    policies = load_policies(args.policies)
    # flush: con la salida redirigida a un log, sin esto no se ve el avance hasta el final
    print(f"{args.scenario}: {changes} cambios, umbral {args.stress_threshold}, repeticiones {reps}", flush=True)

    started = datetime.now(timezone.utc)
    env = environment()  # al arrancar: el commit con el que corre el codigo
    summaries, files = [], []
    for rep in reps:
        plan = build_plan(policies, args.scenario, changes, rep_seed(args.seed, rep))
        run = run_once(policies, plan, args, louvain_seed=rep_seed(args.seed, rep))
        path = out_dir / f"{args.scenario}_{tag}_r{rep:02d}.json"
        path.write_text(json.dumps(run["rows"], ensure_ascii=False), encoding="utf-8")
        s = {"rep": rep, "plan_seed": rep_seed(args.seed, rep), "plan_counts": plan_counts(plan), **summarize(run)}
        summaries.append(s)
        files.append(path.name)
        full = f"{s['mean_t_networkx_s'] * 1e3:.2f} ms" if s["mean_t_networkx_s"] is not None else "sin medir"
        diff = f"{s['max_abs_q_diff']:.1e}" if s["max_abs_q_diff"] is not None else "-"
        print(f"  r{rep:02d}: {s['rebuilds']} reconstrucciones, estrés máx {s['max_stress_q']:.4f}, "
              f"Q completa {full}, incremental {s['mean_t_incremental_s'] * 1e6:.1f} us, |dQ| máx {diff}", flush=True)

    sample = args.networkx_sample
    meta = {
        "script": "paper1_dinamica/run_dynamics.py",
        "argv": sys.argv[1:] if argv is None else argv,
        "params": {**vars(args), "changes": changes, "tag": tag,
                   "mixed_weights": MIXED_WEIGHTS if args.scenario == "mixed" else None},
        **base_meta(args, started, env),
        "plan_seeds": [s["plan_seed"] for s in summaries],
        "louvain_seeds": [s["louvain_seed"] for s in summaries],
        "networkx_sample": {
            "changes_measured": len(networkx_changes(changes, sample)),
            "note": ("Q completa en todos los cambios" if sample is None or sample >= changes else
                     f"Q completa solo en {len(networkx_changes(changes, sample))} de {changes} cambios, "
                     "repartidos parejo; en los demas q_networkx y t_networkx_s son null"),
        },
        "columns": {
            "q_incremental": "Q incremental (la que mueve el estrés); q_real es la misma",
            "q_networkx": "Q completa con networkx sobre el mismo grafo, solo para verificar (null si no se midio)",
            "t_networkx_s": "tiempo de la Q completa; t_real_s es el mismo",
            "t_incremental_s": "tiempo de actualizar L_c, D_c y m con los vecinos de los nodos que cambiaron",
            "t_graph_s": "tiempo de actualizar nodos y aristas; lo necesitan la Q completa y la incremental",
            "rebuilds": "reconstrucciones hechas hasta este cambio, inclusive",
            "change": "cambio aplicado: add, update, remove, deactivate o reactivate",
            "nodes, edges": "tamano del grafo despues del cambio",
        },
        "files": files,
        "replications": summaries,
    }
    if meta["timing"] == TIMING_NOT_REPORTED:
        meta["timing_note"] = "corrida sin TIMING=1: las columnas de tiempo se guardan pero no van al paper"
    # una sola repeticion: su meta va aparte y los resumen_dN.py los juntan
    suffix = f"_r{args.rep:02d}" if args.rep is not None else ""
    meta_path = out_dir / f"{args.scenario}_{tag}{suffix}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Resultados en {out_dir}/ ({meta_path.name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
