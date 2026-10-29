import copy
import random
from pathlib import Path

import pytest

from devac.communities import CommunityDetector
from devac.evaluator import CommunityPolicyEvaluator
from devac.graph import GraphBuilder
from devac.loader import PolicyLoader
from devac.monitor import ModularityMonitor

ROOT = Path(__file__).resolve().parents[1]
POLICIES = ROOT / "experiments/nimbuslab_policies.json"
DIMS = ("subject", "asset", "space", "time", "action")


def _snapshot(graph):
    nodes = {n: d for n, d in graph.nodes(data=True)}
    edges = {tuple(sorted((u, v))): d["weight"] for u, v, d in graph.edges(data=True)}
    return nodes, edges


def _reference(policies, builder):
    ref = GraphBuilder()
    ref.build_event_graph(list(policies), similarity_threshold=builder.similarity_threshold,
                          export=False, weights=builder.weights)
    return _snapshot(ref.graph)


# copia de una politica con un atributo tomado de otra, para que aparezcan y desaparezcan aristas
def _variant(policy, donor, rng, suffix):
    new = copy.deepcopy(policy)
    new.policy_id = f"{policy.policy_id}-{suffix}"
    for k, event in enumerate(new.events):
        event.event_id = f"{new.policy_id}-e{k}"
        source = rng.choice(donor.events)
        dim = rng.choice(DIMS)
        getattr(event, dim).value = copy.deepcopy(getattr(source, dim).value)
    return new


def _random_ops(rng, active, pool, steps):
    for step in range(steps):
        kind = rng.choice(["add", "remove", "update"]) if len(active) > 5 else "add"
        if kind == "add":
            base = rng.choice(pool)
            yield "add", _variant(base, rng.choice(pool), rng, f"a{step}")
        elif kind == "remove":
            yield "remove", active[rng.choice(sorted(active))]
        else:
            old = active[rng.choice(sorted(active))]
            new = _variant(old, rng.choice(pool), rng, f"u{step}")
            new.policy_id = old.policy_id  # misma politica, eventos nuevos
            yield "update", (old, new)


@pytest.mark.parametrize("seed", [1, 2])
def test_grafo_incremental_igual_a_build_con_pesos_congelados(seed):
    rng = random.Random(seed)
    pool = PolicyLoader.from_file(str(POLICIES))
    active = {p.policy_id: p for p in copy.deepcopy(pool[:60])}
    builder = GraphBuilder()
    builder.build_event_graph(list(active.values()), export=False)
    weights = dict(builder.weights)

    for kind, item in _random_ops(rng, active, pool, steps=150):
        if kind == "add":
            builder.add_events(item)
            active[item.policy_id] = item
        elif kind == "remove":
            builder.remove_events(e.event_id for e in item.events)
            del active[item.policy_id]
        else:
            old, new = item
            builder.remove_events(e.event_id for e in old.events)
            builder.add_events(new)
            active[new.policy_id] = new
        assert _snapshot(builder.graph) == _reference(active.values(), builder), kind

    assert builder.weights == weights  # no se recalcularon


def test_evento_repetido_no_se_inserta_dos_veces():
    policies = PolicyLoader.from_file(str(POLICIES))[:5]
    builder = GraphBuilder()
    builder.build_event_graph(policies, export=False)
    with pytest.raises(ValueError):
        builder.add_events(policies[0])


@pytest.mark.parametrize("audit", [True, False])
def test_el_monitor_mantiene_el_grafo_al_mutar_el_evaluador(audit, capsys):
    pool = PolicyLoader.from_file(str(POLICIES))
    policies = copy.deepcopy(pool[:70])
    builder = GraphBuilder()
    graph = builder.build_event_graph(policies, export=False)
    detector = CommunityDetector()
    _, event_to_community = detector.detect_communities(graph)
    event_to_policy = {e.event_id: p.policy_id for p in policies for e in p.events}
    evaluator = CommunityPolicyEvaluator([], {})
    evaluator.run(policies, detector.map_policies_to_communities(event_to_policy, event_to_community))
    ModularityMonitor(evaluator, builder, detector, threshold=1e9, audit_policies=audit)

    rng = random.Random(5)
    evaluator.add_policy(_variant(pool[80], pool[3], rng, "nueva"))
    evaluator.remove_policy(policies[0].policy_id)
    changed = _variant(policies[1], pool[90], rng, "x")
    changed.policy_id = policies[1].policy_id
    evaluator.update_policy(changed)
    target = evaluator.policy_community[policies[2].policy_id]
    evaluator.move_policy_to_community(policies[3].policy_id, target)

    assert _snapshot(builder.graph) == _reference(evaluator.policies.values(), builder)
