import copy
import random
from pathlib import Path

import pytest

from devac.communities import CommunityDetector
from devac.evaluator import CommunityPolicyEvaluator
from devac.graph import GraphBuilder
from devac.loader import PolicyLoader
from devac.monitor import ModularityMonitor
from tests.test_modularity_incremental import TOL, _networkx_q, _request_for, _snapshot

ROOT = Path(__file__).resolve().parents[1]
POLICIES = ROOT / "experiments/nimbuslab_policies.json"


# louvain con semilla y auditoria: dos copias que arrancan exactamente igual
def _pipeline(threshold, seed=3):
    policies = copy.deepcopy(PolicyLoader.from_file(str(POLICIES)))
    builder = GraphBuilder()
    evaluator = CommunityPolicyEvaluator([], {})
    evaluator.run(policies, {})
    monitor = ModularityMonitor(evaluator, builder, CommunityDetector(seed=seed), threshold=threshold)
    monitor._trigger_rebuild = monitor.rebuild_now  # sin hilos
    monitor.rebuild_now()
    return evaluator, builder, monitor


def _state(evaluator, builder, monitor):
    return {
        "graph": _snapshot(builder.graph),
        "partition": dict(monitor._q.community),  # nodo -> comunidad
        "policy_community": dict(evaluator.policy_community),
        "q": monitor.q,
        "excluded": dict(evaluator.excluded),
        "indexed": {c: sorted(e.event_id for e in evs) for c, evs in evaluator.events_by_comm.items() if evs},
        "rebuilds": monitor.get_stats()["rebuilds"],
    }


# desactivar/activar contra el metodo anterior (update con events=[] y reenviar la politica completa),
# con reconstrucciones por umbral de por medio
@pytest.mark.parametrize("threshold", [1e9, 0.02])
def test_activar_y_desactivar_igual_que_vaciar_y_reenviar(threshold, capsys):
    ev_a, b_a, mon_a = _pipeline(threshold)  # metodos nuevos
    ev_b, b_b, mon_b = _pipeline(threshold)  # metodo anterior
    assert _state(ev_a, b_a, mon_a) == _state(ev_b, b_b, mon_b)

    rng = random.Random(5)
    originals = {pid: copy.deepcopy(p) for pid, p in ev_b.policies.items()}
    inactive = []
    for step in range(120):
        if inactive and (rng.random() < 0.45 or len(inactive) > 30):
            pid = inactive.pop(rng.randrange(len(inactive)))
            ev_a.activate_policy(pid)
            ev_b.update_policy(copy.deepcopy(originals[pid]))  # reenviar la politica
            assert ev_a.policies[pid].active and mon_a.history[-1]["op"] == "activate"
        else:
            pid = rng.choice(sorted(set(ev_a.policies) - set(inactive)))
            inactive.append(pid)
            ev_a.deactivate_policy(pid)
            ev_b.update_policy(ev_b.policies[pid].model_copy(update={"events": []}))  # vaciarla
            # conserva sus eventos, pero no tiene nodos ni comunidad
            assert not ev_a.policies[pid].active
            assert [e.event_id for e in ev_a.policies[pid].events] == [e.event_id for e in originals[pid].events]
            assert pid not in ev_a.policy_community
            assert not any(e.event_id in b_a.graph for e in originals[pid].events)

        state_a, state_b = _state(ev_a, b_a, mon_a), _state(ev_b, b_b, mon_b)
        assert state_a == state_b, (step, pid)
        assert mon_a.history[-1]["delta_q"] == mon_b.history[-1]["delta_q"]
        assert abs(mon_a.q - _networkx_q(b_a.graph, state_a["partition"])) < TOL
        assert ev_a.validate_integrity() == []

    if threshold < 1:
        assert mon_a.get_stats()["rebuilds"] > 1  # hubo reconstrucciones con politicas inactivas
    # las mismas decisiones en los dos, y ninguna viene de una politica inactiva
    for policy in originals.values():
        for event in policy.events:
            a, b = ev_a.evaluate(_request_for(event)), ev_b.evaluate(_request_for(event))
            assert (a["result"], a["policy_id"]) == (b["result"], b["policy_id"])
            assert a["policy_id"] not in inactive
    capsys.readouterr()


# la reconstruccion completa solo usa las activas; las inactivas siguen en el evaluador con sus eventos
def test_reconstruccion_ignora_inactivas(capsys):
    evaluator, builder, monitor = _pipeline(1e9)
    off = sorted(evaluator.policies)[:5]
    for pid in off:
        evaluator.deactivate_policy(pid)
    monitor.rebuild_now()
    for pid in off:
        policy = evaluator.policies[pid]
        assert not policy.active and policy.events
        assert pid not in evaluator.policy_community
        assert not any(e.event_id in builder.graph for e in policy.events)
        assert not any(pid in pair for pair in evaluator.excluded.values())  # tampoco en la auditoria
    active = [p for p in evaluator.policies.values() if p.active]
    ref = GraphBuilder()
    ref.build_event_graph(
        [p.model_copy(update={"events": [e for e in p.events if e.event_id not in evaluator.excluded]})
         for p in copy.deepcopy(active)], export=False)
    assert _snapshot(builder.graph) == _snapshot(ref.graph)

    # al activarla vuelve a tener comunidad y nodos
    evaluator.activate_policy(off[0])
    assert off[0] in evaluator.policy_community
    assert all(e.event_id in monitor._q for e in evaluator.policies[off[0]].events
               if e.event_id not in evaluator.excluded)
    assert evaluator.validate_integrity() == []
    capsys.readouterr()


def test_activar_o_desactivar_dos_veces_no_hace_nada(capsys):
    evaluator, _, monitor = _pipeline(1e9)
    pid = sorted(evaluator.policies)[0]
    evaluator.deactivate_policy(pid)
    swaps = len(monitor.history)
    evaluator.deactivate_policy(pid)
    assert len(monitor.history) == swaps
    evaluator.activate_policy(pid)
    evaluator.activate_policy(pid)
    assert len(monitor.history) == swaps + 1
    assert evaluator.deactivate_policy("NO_EXISTE") is None
    capsys.readouterr()
