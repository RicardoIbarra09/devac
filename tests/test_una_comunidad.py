import math
import random
from collections import defaultdict
from itertools import permutations

import networkx as nx
import pytest

from devac.communities import CommunityDetector, best_scored_community, community_order
from devac.evaluator import CommunityPolicyEvaluator
from devac.graph import GraphBuilder
from devac.models import AccessRequest, Policy
from devac.monitor import ModularityMonitor
from tests.test_deny_priority import comp, make_event

TOL = 1e-9


def _policy(pid, *events, effect="allow"):
    return Policy(policy_id=pid, description="", effect=effect, events=list(events))


def _request(event):
    return AccessRequest(subject=comp("subject", event.subject.value), asset=comp("asset", event.asset.value),
                         space=comp("space", event.space.value), time=comp("time", "10:00"),
                         action=comp("action", event.action.value))


def _cluster(name, n):
    return [_policy(f"{name}{i}", make_event(f"{name}{i}-e", subject=f"rol_{name}", asset=f"rec_{name}",
                                             space=f"ubi_{name}", action=f"acc_{i}")) for i in range(n)]


def _monitor(policies, seed=1, audit=False):
    evaluator = CommunityPolicyEvaluator()
    evaluator.run(policies, {})
    builder = GraphBuilder()
    builder.export_graph = lambda *a, **k: None
    monitor = ModularityMonitor(evaluator, builder, CommunityDetector(seed=seed), threshold=1e9, audit_policies=audit)
    monitor.rebuild_now()
    return evaluator, builder, monitor


# Q de networkx con cada evento en la comunidad de su politica, sin pasar por el monitor
def _q_by_policy(evaluator, graph):
    groups = defaultdict(set)
    for node, data in graph.nodes(data=True):
        comm = evaluator.policy_community.get(data["policy_id"])
        if comm is not None:
            groups[comm].add(node)
    sub = graph.subgraph(set().union(*groups.values()))
    return nx.community.modularity(sub, list(groups.values()), weight="weight")


# una regla de MULTI cae en el grupo chico y otra en el grande, y louvain las separa
@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("big_first", [True, False])
def test_politica_que_louvain_separa_queda_en_una_comunidad(seed, big_first, capsys):
    big, small = _cluster("g", 7), _cluster("c", 4)
    e_big = make_event("M-g", subject="rol_g", asset="rec_g", space="ubi_g", action="acc_m")
    e_small = make_event("M-c", subject="rol_c", asset="rec_c", space="ubi_c", action="acc_m")
    events = [e_big, e_small] if big_first else [e_small, e_big]
    evaluator, builder, monitor = _monitor((big + small if big_first else small + big) + [_policy("MULTI", *events)], seed)

    louvain = monitor.community_detector.event_to_community
    assert louvain["M-g"] != louvain["M-c"]
    degree = builder.graph.degree(weight="weight")
    assert degree["M-g"] > degree["M-c"]

    listed = [pid for pids in evaluator.policies_by_community.values() for pid in pids]
    assert sorted(listed) == sorted(evaluator.policies)
    assert evaluator.policy_community["MULTI"] == louvain["M-g"]
    assert evaluator.validate_integrity() == []
    # sus dos reglas se siguen encontrando
    for event in (e_big, e_small):
        out = evaluator.evaluate(_request(event))
        assert (out["result"], out["event_id"]) == ("allow", event.event_id)

    # el monitor cuenta las dos reglas en esa comunidad, en la Q incremental y en la completa
    assert monitor._q.community["M-g"] == monitor._q.community["M-c"] == louvain["M-g"]
    expected = _q_by_policy(evaluator, builder.graph)
    assert abs(monitor.q - expected) < TOL
    assert abs(monitor._compute_modularity(builder.graph) - expected) < TOL

    # y lo sigue haciendo con los cambios que vengan (el estres parte de esa Q)
    stress = 0.0
    for change in (lambda: evaluator.deactivate_policy("MULTI"), lambda: evaluator.activate_policy("MULTI"),
                   lambda: evaluator.remove_policy("g0")):
        before = monitor.q
        change()
        stress += abs(monitor.q - before)
        assert abs(monitor.q - _q_by_policy(evaluator, builder.graph)) < TOL
    assert abs(monitor.get_stats()["stress"] - stress) < 1e-6
    capsys.readouterr()


# politicas de varias reglas al azar, con y sin auditoria: la eleccion contra un calculo aparte
@pytest.mark.parametrize("audit", [True, False])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_eleccion_con_politicas_de_varias_reglas(seed, audit, capsys):
    rng = random.Random(seed)
    policies = [
        _policy(f"P{i}", *[make_event(f"P{i}-e{k}", subject=f"s{rng.randrange(6)}", asset=f"a{rng.randrange(6)}",
                                      space=f"u{rng.randrange(6)}", action=f"x{rng.randrange(6)}")
                           for k in range(rng.randint(1, 4))], effect=rng.choice(["allow", "deny"]))
        for i in range(80)
    ]
    policies[0].active = False
    evaluator, builder, monitor = _monitor(policies, seed, audit)

    louvain, graph = monitor.community_detector.event_to_community, builder.graph
    sums = defaultdict(lambda: defaultdict(list))
    for node, data in graph.nodes(data=True):
        sums[data["policy_id"]][louvain[node]] += [d["weight"] for d in graph[node].values()]
    assert any(len(s) > 1 for s in sums.values())  # louvain si separo alguna
    expected = {}
    for pid, by_comm in sums.items():
        totals = {c: round(math.fsum(w), 9) for c, w in by_comm.items()}
        expected[pid] = min(totals, key=lambda c: (-totals[c], c))
    assert evaluator.policy_community == expected

    listed = [pid for pids in evaluator.policies_by_community.values() for pid in pids]
    assert len(listed) == len(set(listed)) and set(listed) == set(expected) and "P0" not in listed
    assert abs(monitor.q - _q_by_policy(evaluator, graph)) < TOL
    assert abs(monitor._compute_modularity(graph) - monitor.q) < TOL
    capsys.readouterr()


# --- desempate ---

# (encontrada primero, encontrada despues, la que debe ganar)
TIES = [(7, 3, 3), (3, 7, 3), ("c10", "c5", "c5"), ("c5", "c10", "c5"), ("c5", 9, 9), (9, "c5", 9)]


# las dos comunidades con el mismo grado para la politica
@pytest.mark.parametrize("first, second, winner", TIES)
def test_empate_de_grado_en_la_reconstruccion(first, second, winner):
    graph = nx.Graph()
    graph.add_edge("e1", "x", weight=0.5)
    graph.add_edge("e2", "y", weight=0.5)
    owner = {"e1": "P", "e2": "P", "x": "X", "y": "Y"}
    comms = {"e1": first, "x": first, "e2": second, "y": second}
    pbc = CommunityDetector().map_policies_to_communities(owner, comms, graph)
    assert [c for c, pids in pbc.items() if "P" in pids] == [winner]


def test_sin_aristas_tambien_es_empate():
    graph = nx.Graph()
    graph.add_nodes_from(["e1", "e2"])
    pbc = CommunityDetector().map_policies_to_communities({"e1": "P", "e2": "P"}, {"e1": 4, "e2": 1}, graph)
    assert pbc == {1: ["P"]}


# cuatro eventos iguales repartidos en dos comunidades: una politica igual gana lo mismo en las dos
@pytest.mark.parametrize("first, second, winner", TIES)
def test_empate_de_ganancia_en_best_community(first, second, winner, capsys):
    same = [_policy(f"S{i}", make_event(f"S{i}-e")) for i in range(4)]
    other = [_policy(f"O{i}", make_event(f"O{i}-e", f"r{i}", f"a{i}", f"u{i}", action=f"x{i}")) for i in range(4)]
    builder = GraphBuilder()
    builder.build_event_graph(same + other, export=False)
    evaluator = CommunityPolicyEvaluator()
    evaluator.run(same + other, {first: ["S0", "S1"], second: ["S2", "S3"], 100: [p.policy_id for p in other]})
    monitor = ModularityMonitor(evaluator, builder, CommunityDetector(seed=1), threshold=1e9, audit_policies=False)

    new = _policy("N", make_event("N-e"))
    assert monitor.best_community(new) == winner
    evaluator.add_policy(new)
    assert evaluator.policy_community["N"] == winner
    # con ids mezclados lo demas tambien funciona
    evaluator.deactivate_policy("S0")
    evaluator.activate_policy("S0")
    assert evaluator.validate_integrity() == []
    assert abs(monitor.q - _q_by_policy(evaluator, builder.graph)) < TOL
    monitor.rebuild_now()
    assert evaluator.validate_integrity() == []
    capsys.readouterr()


# el resultado no depende del orden en que se encuentran las comunidades, ni del redondeo
def test_desempate_determinista_con_ids_mezclados():
    ids = [10, 2, "c10", "c5", "norte"]
    for order in permutations(ids):
        assert sorted(order, key=community_order) == [2, 10, "c5", "c10", "norte"]
        assert best_scored_community({c: 0.5 for c in order}) == 2
        assert best_scored_community({c: (0.5 if c == "norte" else 0.4) for c in order}) == "norte"
    a, b = 0.1 + 0.2 + 0.3, 0.3 + 0.2 + 0.1
    assert a > b  # el mismo numero sumado en otro orden
    assert best_scored_community({8: a, 1: b}) == 1
    assert best_scored_community({8: 0.3 + 1e-6, 1: 0.3}) == 8
