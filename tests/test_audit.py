from devac import (
    AttributeComponent, CommunityDetector, CommunityPolicyEvaluator, Event,
    GraphBuilder, ModularityMonitor, Policy,
)
from devac.audit import EventRelation, audit_and_mitigate, compare_events


def _comp(attribute: str, value) -> AttributeComponent:
    return AttributeComponent(attribute=attribute, value=value)


def _event(eid, subject="medico", asset="historia", space="urgencias", time="*", action="leer"):
    return Event(
        event_id=eid,
        subject=_comp("rol", subject),
        asset=_comp("recurso", asset),
        space=_comp("ubicacion", space),
        time=_comp("rango_horario", time),
        action=_comp("accion", action),
    )


def _policy(pid, subject, asset, space, time, action, effect, reconfigure_on=None):
    return Policy(
        policy_id=pid, description=pid, effect=effect,
        events=[_event(f"{pid}-e1", subject, asset, space, time, action)],
        reconfigure_on=reconfigure_on or [],
    )


# politicas de relleno para que la entropia no salga en cero
def _diversity_fillers():
    return [
        _policy("FILLER-1", "medico_relleno", "recurso_relleno_1", "zona_relleno_1", "manana", "consultar", "allow"),
        _policy("FILLER-2", "invitado_relleno", "recurso_relleno_2", "zona_relleno_2", "noche", "auditar", "deny"),
    ]


def _build(policies):
    graph = GraphBuilder().build_event_graph(policies)
    detector = CommunityDetector()
    _, event_to_community = detector.detect_communities(graph)
    event_to_policy = {e.event_id: p.policy_id for p in policies for e in p.events}
    return detector.map_policies_to_communities(event_to_policy, event_to_community)


def test_identical_events_are_equal():
    assert compare_events(_event("a"), _event("b")) == EventRelation.EQUAL


def test_wildcard_is_superset_of_exact():
    wide = _event("a", space="*")
    narrow = _event("b", space="urgencias")
    assert compare_events(wide, narrow) == EventRelation.SUPERSET
    assert compare_events(narrow, wide) == EventRelation.SUBSET


def test_disjoint_exact_values():
    e1 = _event("a", subject="medico")
    e2 = _event("b", subject="enfermero")
    assert compare_events(e1, e2) == EventRelation.DISJOINT


def test_in_sets_partial_overlap():
    e1 = _event("a", subject={"in": ["medico", "enfermero"]})
    e2 = _event("b", subject={"in": ["enfermero", "residente"]})
    assert compare_events(e1, e2) == EventRelation.OVERLAP


def test_time_ranges_nested_and_overlapping():
    outer = _event("a", time={"range": ["06:00", "22:00"]})
    inner = _event("b", time={"range": ["08:00", "20:00"]})
    assert compare_events(outer, inner) == EventRelation.SUPERSET

    left = _event("c", time={"range": ["08:00", "14:00"]})
    right = _event("d", time={"range": ["12:00", "20:00"]})
    assert compare_events(left, right) == EventRelation.OVERLAP


def test_single_disjoint_attribute_makes_whole_events_disjoint():
    e1 = _event("a", action="leer")
    e2 = _event("b", action="escribir")
    assert compare_events(e1, e2) == EventRelation.DISJOINT


def test_redundant_policy_is_stripped_when_dominated_by_same_effect():
    wide = _policy("P1", {"in": ["medico", "enfermero"]}, "historia", "urgencias", "*", "leer", "allow")
    narrow = _policy("P2", "medico", "historia", "urgencias", "*", "leer", "allow")
    policies = [wide, narrow] + _diversity_fillers()
    pbc = _build(policies)

    clean, _, report = audit_and_mitigate(policies, pbc)

    by_id = {p.policy_id: p for p in clean}
    assert len(by_id["P2"].events) == 0
    assert len(by_id["P1"].events) == 1
    assert len(report.redundant_events) == 1
    assert report.redundant_events[0].policy_id == "P2"
    assert report.redundant_events[0].dominated_by_policy == "P1"


def test_conflicting_policies_are_detected_and_reordered_so_deny_wins():
    allow_p = _policy("P3", "auditor", "log", "zona_a", "mediodia", "leer", "allow")
    deny_p = _policy("P4", "auditor", "log", "zona_a", "mediodia", "leer", "deny")
    filler_1 = _policy("F1", "medico", "historia", "urgencias", "*", "leer", "allow")
    filler_2 = _policy("F2", "invitado", "portal", "red_externa", "*", "lectura", "allow")
    policies = [allow_p, deny_p, filler_1, filler_2]
    pbc = _build(policies)
    for comm, pids in pbc.items():
        if {"P3", "P4"}.issubset(set(pids)):
            # se fuerza el allow primero para ver que el audit lo reordena
            pbc[comm] = ["P3"] + [p for p in pids if p != "P3"]

    _, new_pbc, report = audit_and_mitigate(policies, pbc, conflict_priority="deny")

    assert len(report.conflicts) == 1
    conflicted_comm = [c for c, pids in pbc.items() if {"P3", "P4"}.issubset(set(pids))][0]
    assert new_pbc[conflicted_comm].index("P4") < new_pbc[conflicted_comm].index("P3")


def test_unrelated_policy_is_left_untouched():
    a = _policy("A", "medico", "historia", "urgencias", "*", "leer", "allow")
    b = _policy("B", "medico", "historia", "urgencias", "*", "leer", "allow")
    unrelated = _policy("C", "anonimo", "puerto_22", "red_externa", "noche", "escaneo_puertos", "deny")
    pbc = _build([a, b, unrelated])

    clean, _, report = audit_and_mitigate([a, b, unrelated], pbc)

    by_id = {p.policy_id: p for p in clean}
    assert len(by_id["C"].events) == 1
    assert all(r.policy_id != "C" and r.dominated_by_policy != "C" for r in report.redundant_events)
    assert all(c.policy_a != "C" and c.policy_b != "C" for c in report.conflicts)


def test_wildcard_redundancy_is_found_across_different_communities():
    wide = _policy("ADMIN", "*", "portal_externo", "red_externa", "tarde", "lectura", "allow")
    narrow = _policy("GUEST", "invitado", "portal_externo", "red_externa", "tarde", "lectura", "allow")
    other = _policy("OTHER", "anonimo", "puerto_22", "red_externa2", "noche", "escaneo_puertos", "deny")
    pbc = _build([wide, narrow, other])

    clean, _, report = audit_and_mitigate([wide, narrow, other], pbc)

    by_id = {p.policy_id: p for p in clean}
    assert len(by_id["GUEST"].events) == 0
    assert any(r.policy_id == "GUEST" and r.dominated_by_policy == "ADMIN" for r in report.redundant_events)


def test_duplicate_tiebreak_prefers_the_policy_with_reconfigure_on():
    a_sin_reconf = _policy("A", "medico", "historia", "urgencias", "*", "leer", "allow")
    b_con_reconf = _policy(
        "B", "medico", "historia", "urgencias", "*", "leer", "allow",
        reconfigure_on=["almacen_eliminado"],
    )
    policies = [a_sin_reconf, b_con_reconf] + _diversity_fillers()
    pbc = _build(policies)

    clean, _, report = audit_and_mitigate(policies, pbc)

    by_id = {p.policy_id: p for p in clean}
    assert len(report.redundant_events) == 1
    assert report.redundant_events[0].policy_id == "A"
    assert report.redundant_events[0].dominated_by_policy == "B"
    assert len(by_id["A"].events) == 0
    assert len(by_id["B"].events) == 1
    assert by_id["B"].reconfigure_on == ["almacen_eliminado"]


def test_no_duplicate_reports_for_the_same_pair():
    a = _policy("A", "medico", "historia", "urgencias", "*", "leer", "allow")
    b = _policy("B", "medico", "historia", "urgencias", "*", "leer", "allow")
    policies = [a, b] + _diversity_fillers()
    pbc = _build(policies)

    _, _, report = audit_and_mitigate(policies, pbc)

    assert len(report.redundant_events) == 1


def test_modularity_monitor_runs_audit_during_level4_rebuild():
    wide = _policy("P1", {"in": ["medico", "enfermero"]}, "historia", "urgencias", "*", "leer", "allow")
    narrow = _policy("P2", "medico", "historia", "urgencias", "*", "leer", "allow")
    policies = [wide, narrow] + _diversity_fillers()

    gb = GraphBuilder()
    graph = gb.build_event_graph(policies)
    cd = CommunityDetector()
    _, e2c = cd.detect_communities(graph)
    e2p = {e.event_id: p.policy_id for p in policies for e in p.events}
    pbc = cd.map_policies_to_communities(e2p, e2c)

    ce = CommunityPolicyEvaluator()
    ce.run(policies, pbc)
    assert len(ce.policies["P2"].events) == 1

    monitor = ModularityMonitor(evaluator=ce, graph_builder=gb, community_detector=cd, threshold=0.5)
    monitor._rebuild_worker()  # reconstruccion de nivel 4 sin esperar al umbral

    assert monitor.last_audit_report is not None
    assert len(monitor.last_audit_report.redundant_events) == 1
    assert len(ce.policies["P2"].events) == 0
    assert ce.validate_integrity() == []
