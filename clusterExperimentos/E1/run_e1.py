#!/usr/bin/env python3
# uso:
#   python run_e1.py --workload W1 --policies experiments/nimbuslab_policies.json --seeds 1 2 3 4 5 --n-requests 10000 --out-dir resultados/e1

import argparse
import copy
import csv
import json
import math
import os
import platform
import random
import socket
import sys
import time
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

from devac import (
    AccessRequest,
    AttributeComponent,
    CommunityDetector,
    CommunityPolicyEvaluator,
    GraphBuilder,
    PolicyLoader,
    TraditionalPolicyEvaluator,
    audit_and_mitigate,
)

# sin gexf: es I/O que no queremos en las mediciones
GraphBuilder.export_graph = lambda self, *args, **kwargs: None

FIELDS = [
    ("subject", "rol"),
    ("asset", "recurso"),
    ("space", "ubicacion"),
    ("time", "rango_horario"),
    ("action", "accion"),
]
UNSEEN = "valor_desconocido"  # valor que no aparece en ninguna politica


def _hm(text):
    h, m = text.strip().split(":")
    return int(h) * 60 + int(m)


def _fmt(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


WRAP_AWARE = True  # el oraculo acepta rangos que cruzan medianoche


# el oraculo no usa nada de devac para decidir, solo esto
def _compile_component(value):
    if isinstance(value, dict):
        if "in" in value:
            return ("in", {str(v).strip().lower() for v in value["in"]})
        if "range" in value:
            lo, hi = value["range"]
            return ("range", (_hm(lo), _hm(hi)))
        return ("none", None)
    v = str(value).strip().lower()
    return ("any", None) if v == "*" else ("eq", v)


def _covers(comp, req_value):
    kind, data = comp
    if kind == "any":
        return True
    if kind == "eq":
        return req_value == data
    if kind == "in":
        return req_value in data
    if kind == "range":
        try:
            point = _hm(req_value)
        except ValueError:
            return False
        if data[0] <= data[1]:
            return data[0] <= point <= data[1]
        return WRAP_AWARE and (point >= data[0] or point <= data[1])  # cruza medianoche
    return False


def compile_policies(policies):
    out = []
    for p in policies:
        for e in p.events:
            comps = [_compile_component(getattr(e, fld).value) for fld, _ in FIELDS]
            out.append((p.effect, comps))
    return out


# deny gana; si nada cubre la peticion tambien es deny
def oracle_decision(compiled, req_values):
    has_allow = False
    covered = False
    for effect, comps in compiled:
        if all(_covers(c, v) for c, v in zip(comps, req_values)):
            covered = True
            if effect == "deny":
                return "deny", True
            has_allow = True
    return ("allow" if has_allow else "deny"), covered


# valores que si aparecen en las politicas, por atributo
def build_vocab(policies):
    vocab = {attr: set() for _, attr in FIELDS}
    for p in policies:
        for e in p.events:
            for fld, attr in FIELDS:
                v = getattr(e, fld).value
                if isinstance(v, str):
                    if v.strip() != "*":
                        vocab[attr].add(v.strip().lower())
                elif isinstance(v, dict) and "in" in v:
                    vocab[attr].update(str(x).strip().lower() for x in v["in"])
    return {attr: sorted(vals) for attr, vals in vocab.items()}


# peticion armada a partir de un evento, para que si haga match
def _matching_request(rng, events, vocab):
    for _ in range(100):
        e = rng.choice(events)
        values = []
        ok = True
        for fld, attr in FIELDS:
            v = getattr(e, fld).value
            if isinstance(v, dict) and "range" in v:
                lo, hi = _hm(v["range"][0]), _hm(v["range"][1])
                if lo > hi:
                    if not WRAP_AWARE:
                        ok = False
                        break
                    span = (1440 - lo) + hi + 1  # minutos dentro del rango que cruza medianoche
                    values.append(_fmt((lo + rng.randrange(span)) % 1440))
                else:
                    values.append(_fmt(rng.randint(lo, hi)))
            elif isinstance(v, dict) and "in" in v:
                values.append(str(rng.choice(sorted(v["in"]))).strip().lower())
            elif str(v).strip() == "*":
                if attr == "rango_horario":
                    values.append(_fmt(rng.randint(0, 1439)))
                else:
                    values.append(rng.choice(vocab[attr]) if vocab[attr] else UNSEEN)
            else:
                values.append(str(v).strip().lower())
        if ok:
            return values
    return None


# peticion al azar, la mayoria no va a coincidir con nada
def _random_request(rng, vocab):
    values = []
    for _, attr in FIELDS:
        pool = vocab[attr]
        if attr == "rango_horario":
            if pool and rng.random() < 0.5:
                values.append(rng.choice(pool))
            else:
                values.append(_fmt(rng.randint(0, 1439)))
        elif not pool or rng.random() < 0.15:
            values.append(UNSEEN)
        else:
            values.append(rng.choice(pool))
    return values


def generate_requests(policies, compiled, n, seed):
    rng = random.Random(seed)
    vocab = build_vocab(policies)
    events = [e for p in policies for e in p.events]
    out = []
    failed = 0
    for i in range(n):
        want_match = i % 2 == 0  # mitad con match, mitad sin
        req = None
        if want_match:
            req = _matching_request(rng, events, vocab)
            if req is None:
                failed += 1
        else:
            for _ in range(500):
                cand = _random_request(rng, vocab)
                _, covered = oracle_decision(compiled, cand)
                # se busca una que de verdad no cubra ninguna politica
                if not covered:
                    req = cand
                    break
            if req is None:
                failed += 1
        if req is None:
            req = _random_request(rng, vocab)
        out.append(req)
    rng.shuffle(out)
    return out, failed


def to_access_request(values):
    comps = {fld: AttributeComponent(attribute=attr, value=v) for (fld, attr), v in zip(FIELDS, values)}
    return AccessRequest(**comps)


# grafo + louvain
def build_structure(policies):
    graph = GraphBuilder().build_event_graph(policies)
    detector = CommunityDetector()
    _, event_to_community = detector.detect_communities(graph)
    event_to_policy = {e.event_id: p.policy_id for p in policies for e in p.events}
    return detector.map_policies_to_communities(event_to_policy, event_to_community)


def decisions(evaluator, requests):
    t0 = time.perf_counter()
    out = [evaluator.evaluate(to_access_request(r))["result"] for r in requests]
    return out, time.perf_counter() - t0


def confusion(pred, truth):
    tp = sum(p == "allow" and t == "allow" for p, t in zip(pred, truth))
    tn = sum(p == "deny" and t == "deny" for p, t in zip(pred, truth))
    fp = sum(p == "allow" and t == "deny" for p, t in zip(pred, truth))
    fn = sum(p == "deny" and t == "allow" for p, t in zip(pred, truth))
    return tp, tn, fp, fn


def ratio(a, b):
    return a / b if b else float("nan")


def agreement(a, b):
    return 100.0 * sum(x == y for x, y in zip(a, b)) / len(a)


def run_one(workload, policies_path, seed, n_requests, audit_on):
    random.seed(seed)
    base = PolicyLoader.from_file(policies_path)
    pols = copy.deepcopy(base)

    t0 = time.perf_counter()
    pbc = build_structure(pols)
    build_s = time.perf_counter() - t0

    audit = None
    audit_s = 0.0
    if audit_on:
        t0 = time.perf_counter()
        pols, pbc, audit = audit_and_mitigate(pols, pbc)
        audit_s = time.perf_counter() - t0

    devac = CommunityPolicyEvaluator()
    devac.run(pols, pbc)
    linear = TraditionalPolicyEvaluator(copy.deepcopy(pols))

    ref_compiled = compile_policies(base)  # el oraculo usa las politicas originales, sin auditar
    requests, failed = generate_requests(base, ref_compiled, n_requests, seed)
    truth, covered = [], 0
    for r in requests:
        d, c = oracle_decision(ref_compiled, r)
        truth.append(d)
        covered += c

    d_devac, t_devac = decisions(devac, requests)
    d_lin, t_lin = decisions(linear, requests)

    tp, tn, fp, fn = confusion(d_devac, truth)
    precision = ratio(tp, tp + fp)
    recall = ratio(tp, tp + fn)
    f1 = ratio(2 * tp, 2 * tp + fp + fn)

    preserved = float("nan")
    if audit_on:
        clean_compiled = compile_policies(pols)
        # la auditoria no debe cambiar ninguna decision
        preserved = agreement(truth, [oracle_decision(clean_compiled, r)[0] for r in requests])

    # cuantos rangos cruzan medianoche en la carga
    wrapped = sum(
        1
        for p in base
        for e in p.events
        if isinstance(e.time.value, dict) and "range" in e.time.value
        and _hm(e.time.value["range"][0]) > _hm(e.time.value["range"][1])
    )
    return {
        "workload": workload,
        "seed": seed,
        "n_policies": len(base),
        "n_events": sum(len(p.events) for p in base),
        "n_requests": n_requests,
        "frac_covered_pct": round(100.0 * covered / n_requests, 2),
        "match_gen_failures": failed,
        "audit": "on" if audit_on else "off",
        "n_communities": len(pbc),
        "agree_devac_linear_pct": round(agreement(d_devac, d_lin), 4),
        "agree_devac_oracle_pct": round(agreement(d_devac, truth), 4),
        "agree_linear_oracle_pct": round(agreement(d_lin, truth), 4),
        "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "accuracy": round(ratio(tp + tn, len(requests)), 6),
        "precision": round(precision, 6) if not math.isnan(precision) else "",
        "recall": round(recall, 6) if not math.isnan(recall) else "",
        "f1": round(f1, 6) if not math.isnan(f1) else "",
        "redundant_events": len(audit.redundant_events) if audit else "",
        "conflicts": len(audit.conflicts) if audit else "",
        "reordered_communities": len(audit.reordered_communities) if audit else "",
        "events_compared": audit.events_compared if audit else "",
        "audit_seconds": round(audit_s, 4) if audit_on else "",
        "decisions_preserved_pct": round(preserved, 4) if audit_on else "",
        "wrapped_midnight_ranges": wrapped,
        "build_seconds": round(build_s, 3),
        "devac_eval_seconds": round(t_devac, 3),
        "linear_eval_seconds": round(t_lin, 3),
    }


def cpu_model():
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor()


def versions():
    out = {}
    for pkg in ("pydantic", "networkx", "numpy"):
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            out[pkg] = None
    return out


def main():
    ap = argparse.ArgumentParser(description="E1: correctitud de DEvAC y del evaluador lineal contra el oraculo")
    ap.add_argument("--workload", required=True, help="Nombre de la carga: W1, W2, W3...")
    ap.add_argument("--policies", required=True, help="JSON de politicas de la carga")
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3, 4, 5])
    ap.add_argument("--n-requests", type=int, default=10000)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--demo-semantics", action="store_true",
                    help="El oraculo replica el demo (un rango 23:00-06:00 nunca coincide). Por defecto el "
                         "oraculo usa la semantica prevista (cruce de medianoche) y expone esa discrepancia.")
    args = ap.parse_args()
    global WRAP_AWARE
    WRAP_AWARE = not args.demo_semantics

    if not Path(args.policies).is_file():
        sys.exit(f"[E1] No existe el archivo de politicas: {args.policies}")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for seed in args.seeds:
        # cada semilla se corre sin y con auditoria
        for audit_on in (False, True):
            row = run_one(args.workload, args.policies, seed, args.n_requests, audit_on)
            rows.append(row)
            print(
                f"[E1] {args.workload} semilla={seed} auditoria={row['audit']:>3} "
                f"concordancia_lineal={row['agree_devac_linear_pct']:.3f}% "
                f"vs_oraculo={row['agree_devac_oracle_pct']:.3f}% FP={row['fp']} FN={row['fn']}",
                flush=True,
            )

    csv_path = out_dir / f"e1_{args.workload}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    on = [r for r in rows if r["audit"] == "on"]
    # criterios de aceptacion de E1
    criterio = {
        "concordancia_100_con_auditoria": all(r["agree_devac_linear_pct"] == 100.0 for r in on),
        "decisiones_preservadas_100": all(r["decisions_preserved_pct"] == 100.0 for r in on),
        "fp_cero": all(r["fp"] == 0 for r in on),
    }
    meta = {
        "experimento": "E1",
        "carga": args.workload,
        "politicas": args.policies,
        "semillas": args.seeds,
        "n_solicitudes": args.n_requests,
        "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "nodo": socket.gethostname(),
        "cpu": cpu_model(),
        "python": platform.python_version(),
        "dependencias": versions(),
        "commit_demo": os.environ.get("DEVAC_COMMIT", "desconocido"),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "slurm_array_task_id": os.environ.get("SLURM_ARRAY_TASK_ID"),
        "oraculo_cruce_medianoche": WRAP_AWARE,
        "clase_positiva": "allow",
        "criterios_de_aceptacion": criterio,
    }
    with open(out_dir / f"e1_{args.workload}_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    print("[E1] Criterios con auditoria activada:", json.dumps(criterio, ensure_ascii=False))
    print(f"[E1] Resultados: {csv_path}")


if __name__ == "__main__":
    main()
