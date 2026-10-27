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


# peticion concreta que cumple el evento: de un rango o un conjunto toma el primer valor
def _request_for(event):
    def concrete(value):
        if isinstance(value, dict):
            return str((value.get("range") or value.get("in"))[0])
        return str(value)

    return AccessRequest(**{
        dim: AttributeComponent(attribute=getattr(event, dim).attribute, value=concrete(getattr(event, dim).value))
        for dim in DIMS
    })


def _snapshot(graph):
    nodes = {n: d for n, d in graph.nodes(data=True)}
    edges = {tuple(sorted((u, v))): d["weight"] for u, v, d in graph.edges(data=True)}
    return nodes, edges


# dos copias independientes que arrancan de la misma particion (louvain no es determinista)
def _twins(policies):
    graph = GraphBuilder().build_event_graph(copy.deepcopy(policies), export=False)
    detector = CommunityDetector()
    _, event_to_community = detector.detect_communities(graph)
    event_to_policy = {e.event_id: p.policy_id for p in policies for e in p.events}
    pbc = detector.map_policies_to_communities(event_to_policy, event_to_community)
    twins = []
    for _ in range(2):
        own = copy.deepcopy(policies)
        builder = GraphBuilder()
        builder.build_event_graph(own, export=False)
        evaluator = CommunityPolicyEvaluator([], {})
        evaluator.run(own, copy.deepcopy(pbc))
        monitor = ModularityMonitor(evaluator, builder, detector, threshold=1e9, audit_policies=False)
        twins.append((evaluator, builder, monitor))
    return twins


def test_desactivar_con_events_vacio_equivale_a_borrar(capsys):
    rng = random.Random(8)
    pool = PolicyLoader.from_file(str(POLICIES))
    (ev_a, b_a, mon_a), (ev_b, b_b, mon_b) = _twins(pool)
    assert _snapshot(b_a.graph) == _snapshot(b_b.graph)

    for pid in rng.sample(sorted(ev_a.policies), 40):
        ev_a.update_policy(ev_a.policies[pid].model_copy(update={"events": []}))  # desactivar
        ev_b.remove_policy(pid)  # borrar
        assert _snapshot(b_a.graph) == _snapshot(b_b.graph), pid
        assert mon_a.q == mon_b.q, pid
        # el proxy mira la version anterior en los dos casos
        assert mon_a.history[-1]["delta_proxy"] == mon_b.history[-1]["delta_proxy"], pid
        expected = _networkx_q(b_a.graph, _evaluator_partition(ev_a, b_a.graph))
        assert abs(mon_a.q - expected) < TOL

    assert sum(row["delta_proxy"] for row in mon_a.history) > 0
    # la desactivada sigue registrada y en su comunidad, sin nodos
    assert len(ev_a.policies) == len(ev_b.policies) + 40
    capsys.readouterr()


def test_reactivar_vuelve_a_poner_los_nodos(capsys):
    pool = PolicyLoader.from_file(str(POLICIES))
    evaluator, builder, monitor = _pipeline(copy.deepcopy(pool), 1e9)
    before = _snapshot(builder.graph)
    q_before = monitor.q
    target = sorted(evaluator.policies)[7]
    original = evaluator.policies[target]
    evaluator.update_policy(original.model_copy(update={"events": []}))
    evaluator.update_policy(original)
    assert _snapshot(builder.graph) == before and abs(monitor.q - q_before) < TOL

    # despues de una reconstruccion la desactivada se queda sin comunidad; al reactivar se le asigna una
    evaluator.update_policy(original.model_copy(update={"events": []}))
    monitor.rebuild_now()
    assert target not in evaluator.policy_community
    evaluator.update_policy(original)
    assert target in evaluator.policy_community
    assert all(e.event_id in monitor._q for e in original.events)
    capsys.readouterr()


def _used_reference(evaluator, weights=None):
    used = [p.model_copy(update={"events": [e for e in p.events if e.event_id not in evaluator.excluded]})
            for p in copy.deepcopy(list(evaluator.policies.values()))]
    ref = GraphBuilder()
    ref.build_event_graph(used, export=False, weights=weights)
    return ref


# nimbuslab mas AMPLIA, que cubre a ESTRECHA con el mismo efecto
def _with_dominated(seed=None):
    pool = PolicyLoader.from_file(str(POLICIES))
    wide = copy.deepcopy(pool[0])
    wide.policy_id = "AMPLIA"
    wide.events = wide.events[:1]
    wide.events[0].event_id = "AMPLIA-e1"
    wide.events[0].space.value = "*"  # cubre a la de abajo
    narrow = copy.deepcopy(wide)
    narrow.policy_id = "ESTRECHA"
    narrow.events[0].event_id = "ESTRECHA-e1"
    narrow.events[0].space.value = pool[0].events[0].space.value
    policies = copy.deepcopy(pool) + [wide, narrow]

    builder = GraphBuilder()
    evaluator = CommunityPolicyEvaluator([], {})
    evaluator.run(policies, {})
    monitor = ModularityMonitor(evaluator, builder, CommunityDetector(seed=seed), threshold=1e9)
    monitor.rebuild_now()  # louvain + auditoria
    return evaluator, builder, monitor


def test_eventos_excluidos_no_estan_en_el_grafo_ni_en_q(capsys):
    evaluator, builder, monitor = _with_dominated()
    assert evaluator.excluded.get("ESTRECHA-e1") == ("ESTRECHA", "AMPLIA")

    # build completo: sin los excluidos y con pesos calculados sobre lo que queda
    assert all(eid not in builder.graph for eid in evaluator.excluded)
    ref = _used_reference(evaluator)
    assert _snapshot(builder.graph) == _snapshot(ref.graph) and builder.weights == ref.weights
    assert abs(monitor.q - _networkx_q(builder.graph, _evaluator_partition(evaluator, builder.graph))) < TOL

    # incremental: al borrar la que cubre, el evento liberado entra al grafo y a Q
    evaluator.remove_policy("AMPLIA")
    assert "ESTRECHA-e1" not in evaluator.excluded
    assert "ESTRECHA-e1" in builder.graph and "ESTRECHA-e1" in monitor._q
    assert _snapshot(builder.graph) == _snapshot(_used_reference(evaluator, builder.weights).graph)
    assert abs(monitor.q - _networkx_q(builder.graph, _evaluator_partition(evaluator, builder.graph))) < TOL
    assert monitor.history[-1]["op"] == "remove"  # un solo cambio, no dos
    capsys.readouterr()


def test_particion_final_es_louvain_sobre_el_grafo_usado(capsys):
    evaluator, builder, monitor = _with_dominated(seed=5)
    stats = monitor.last_rebuild_stats
    assert stats["excluded"] == len(evaluator.excluded) > 0

    # louvain con la misma semilla sobre el grafo sin excluidos da exactamente la particion del evaluador
    ref = _used_reference(evaluator)
    _, event_to_community = CommunityDetector().detect_communities(ref.graph, seed=stats["louvain_seed"])
    groups = defaultdict(set)
    for node, comm in event_to_community.items():
        groups[comm].add(ref.graph.nodes[node]["policy_id"])
    assert {frozenset(pids) for pids in evaluator.policies_by_community.values()} == {frozenset(g) for g in groups.values()}
    assert evaluator.validate_integrity() == []

    # ESTRECHA tenia todo excluido y se quedo sin comunidad; al borrar AMPLIA se le asigna una
    assert "ESTRECHA" not in evaluator.policy_community
    request = _request_for(evaluator.policies["ESTRECHA"].events[0])
    assert evaluator.evaluate(request)["policy_id"] == "AMPLIA"
    evaluator.remove_policy("AMPLIA")
    assert "ESTRECHA" in evaluator.policy_community
    assert evaluator.evaluate(_request_for(evaluator.policies["ESTRECHA"].events[0]))["policy_id"] == "ESTRECHA"
    assert abs(monitor.q - _networkx_q(builder.graph, _evaluator_partition(evaluator, builder.graph))) < TOL
    capsys.readouterr()
