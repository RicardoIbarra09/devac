import copy
import random
from collections import defaultdict
from pathlib import Path

import networkx as nx
import pytest

from devac.communities import CommunityDetector
from devac.evaluator import CommunityPolicyEvaluator
from devac.graph import GraphBuilder
from devac.loader import PolicyLoader
from devac.models import AccessRequest, AttributeComponent
from devac.modularity import IncrementalModularity
from devac.monitor import ModularityMonitor

ROOT = Path(__file__).resolve().parents[1]
POLICIES = ROOT / "experiments/nimbuslab_policies.json"
DIMS = ("subject", "asset", "space", "time", "action")
TOL = 1e-9


def _networkx_q(graph, node_community):
    communities = defaultdict(set)
    for node, comm in node_community.items():
        communities[comm].add(node)
    sub = graph.subgraph(node_community)
    if sub.number_of_edges() == 0:
        return 0.0
    return nx.community.modularity(sub, list(communities.values()), weight="weight")


def test_q_incremental_en_grafo_aleatorio():
    rng = random.Random(7)
    graph = nx.Graph()
    state = IncrementalModularity()
    truth = {}  # nodo -> comunidad, solo los cubiertos
    for step in range(1000):
        if graph.number_of_nodes() < 10 or rng.random() < 0.55:
            node = f"n{step}"
            graph.add_node(node)
            for other in rng.sample(sorted(graph.nodes), min(4, graph.number_of_nodes() - 1)):
                if other != node:
                    graph.add_edge(node, other, weight=rng.uniform(0.3, 1.0))
            if rng.random() < 0.9:  # algunos nodos se quedan sin comunidad
                comm = rng.randrange(6)
                state.add_node(graph, node, comm)
                truth[node] = comm
        else:
            node = rng.choice(sorted(graph.nodes))
            state.remove_node(graph, node)
            graph.remove_node(node)
            truth.pop(node, None)
        assert abs(state.q - _networkx_q(graph, truth)) < TOL, step


def _variant(policy, donor, rng, suffix):
    new = copy.deepcopy(policy)
    new.policy_id = f"{policy.policy_id}-{suffix}"
    for k, event in enumerate(new.events):
        event.event_id = f"{new.policy_id}-e{k}"
        dim = rng.choice(DIMS)
        getattr(event, dim).value = copy.deepcopy(getattr(rng.choice(donor.events), dim).value)
    return new


def _pipeline(policies, threshold):
    builder = GraphBuilder()
    graph = builder.build_event_graph(policies, export=False)
    detector = CommunityDetector()
    _, event_to_community = detector.detect_communities(graph)
    event_to_policy = {e.event_id: p.policy_id for p in policies for e in p.events}
    evaluator = CommunityPolicyEvaluator([], {})
    evaluator.run(policies, detector.map_policies_to_communities(event_to_policy, event_to_community))
    monitor = ModularityMonitor(evaluator, builder, detector, threshold=threshold, audit_policies=False)
    monitor._trigger_rebuild = monitor.rebuild_now  # sin hilos
    return evaluator, builder, monitor


# particion leida del grafo y del evaluador, sin pasar por el monitor
def _evaluator_partition(evaluator, graph):
    return {
        node: evaluator.policy_community[data["policy_id"]]
        for node, data in graph.nodes(data=True)
        if data["policy_id"] in evaluator.policy_community
    }


@pytest.mark.parametrize("threshold", [1e9, 0.05])  # sin reconstrucciones y con varias
def test_q_incremental_del_monitor_contra_networkx(threshold, capsys):
    rng = random.Random(11)
    pool = PolicyLoader.from_file(str(POLICIES))
    evaluator, builder, monitor = _pipeline(copy.deepcopy(pool), threshold)

    for step in range(1000):
        active = sorted(evaluator.policies)
        if len(active) < 30 or rng.random() < 0.5:
            evaluator.add_policy(_variant(rng.choice(pool), rng.choice(pool), rng, f"s{step}"))
        else:
            evaluator.remove_policy(rng.choice(active))
        expected = _networkx_q(builder.graph, _evaluator_partition(evaluator, builder.graph))
        assert abs(monitor.q - expected) < TOL, step

    rebuilds = monitor.get_stats()["rebuilds"]
    assert rebuilds == 0 if threshold > 1 else rebuilds > 0
    capsys.readouterr()  # el evaluador imprime cada cambio


def test_verify_modularity_guarda_las_dos_q(capsys):
    rng = random.Random(3)
    pool = PolicyLoader.from_file(str(POLICIES))
    evaluator, _, monitor = _pipeline(copy.deepcopy(pool[:60]), 1e9)
    monitor.verify_modularity = True
    evaluator.add_policy(_variant(pool[70], pool[5], rng, "v"))
    evaluator.remove_policy(pool[0].policy_id)

    for row in monitor.history:
        assert abs(row["q_incremental"] - row["q_networkx"]) < TOL
        assert row["t_networkx_s"] is not None and row["t_incremental_s"] >= 0
    assert [row["op"] for row in monitor.history] == ["add", "remove"]


# la eleccion del monitor contra probar cada opcion con networkx, incluida una comunidad nueva
def test_asignacion_por_ganancia_de_modularidad(capsys):
    rng = random.Random(21)
    pool = PolicyLoader.from_file(str(POLICIES))
    evaluator, builder, monitor = _pipeline(copy.deepcopy(pool), 1e9)
    base = _evaluator_partition(evaluator, builder.graph)
    new_comm = object()
    chose_new = 0

    for case in range(40):
        candidate = _variant(rng.choice(pool), rng.choice(pool), rng, f"g{case}")
        if case % 10 == 0:
            for event in candidate.events:
                for dim in ("subject", "space", "time", "action"):  # solo comparte el recurso
                    getattr(event, dim).value = f"{dim}_sin_vecinos_{case}"
        choice = monitor.best_community(candidate)

        trial = copy.deepcopy(builder)
        added = trial.add_events(copy.deepcopy(candidate))
        scores = {}
        for option in list(evaluator.policies_by_community) + [new_comm]:
            partition = dict(base, **{node: option for node in added})
            scores[option] = _networkx_q(trial.graph, partition)
        best = max(scores.values())
        picked = new_comm if choice is None else choice
        assert scores[picked] > best - 1e-12, case
        chose_new += choice is None

    assert 0 < chose_new < 40  # se probaron los dos caminos
    capsys.readouterr()


def test_politica_sin_vecinos_abre_comunidad(capsys):
    pool = PolicyLoader.from_file(str(POLICIES))
    evaluator, _, monitor = _pipeline(copy.deepcopy(pool[:50]), 1e9)
    lonely = copy.deepcopy(pool[60])
    lonely.policy_id = "SOLA"
    for k, event in enumerate(lonely.events):
        event.event_id = f"SOLA-e{k}"
        for dim in DIMS:
            getattr(event, dim).value = f"{dim}_unico"
    before = set(evaluator.policies_by_community)
    evaluator.add_policy(lonely)
    comm = evaluator.policy_community["SOLA"]
    assert comm not in before and evaluator.policies_by_community[comm] == ["SOLA"]
    assert evaluator.evaluate(_request_for(lonely.events[0]))["policy_id"] == "SOLA"
    capsys.readouterr()


def _request_for(event):
    return AccessRequest(**{
        dim: AttributeComponent(attribute=getattr(event, dim).attribute, value=str(getattr(event, dim).value))
        for dim in DIMS
    })
