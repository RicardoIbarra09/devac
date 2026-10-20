from devac import (
    AccessRequest, AttributeComponent, CommunityDetector,
    CommunityPolicyEvaluator, Event, GraphBuilder, Policy,
)


def _comp(attribute: str, value) -> AttributeComponent:
    return AttributeComponent(attribute=attribute, value=value)


def _build_evaluator(policies):
    graph = GraphBuilder().build_event_graph(policies)
    detector = CommunityDetector()
    _, event_to_community = detector.detect_communities(graph)
    event_to_policy = {e.event_id: p.policy_id for p in policies for e in p.events}
    policies_by_community = detector.map_policies_to_communities(event_to_policy, event_to_community)
    evaluator = CommunityPolicyEvaluator()
    evaluator.run(policies, policies_by_community)
    return evaluator


def _request(time_value: str) -> AccessRequest:
    return AccessRequest(
        subject=_comp("rol", "medico"),
        asset=_comp("recurso", "historia_clinica"),
        space=_comp("ubicacion", "urgencias"),
        time=_comp("rango_horario", time_value),
        action=_comp("accion", "leer"),
    )


def test_string_time_range_is_normalized_to_range_dict():
    component = _comp("rango_horario", "08:00-20:00")
    assert component.value == {"range": ["08:00", "20:00"]}


def test_categorical_time_value_is_left_untouched():
    assert _comp("rango_horario", "muy_larga").value == "muy_larga"
    assert _comp("rango_horario", "media-noche").value == "media-noche"


def test_string_time_range_is_routed_through_interval_tree():
    policy = Policy(
        policy_id="p1",
        description="medicos horario diurno",
        effect="allow",
        events=[Event(
            event_id="e1",
            subject=_comp("rol", "medico"),
            asset=_comp("recurso", "historia_clinica"),
            space=_comp("ubicacion", "urgencias"),
            time=_comp("rango_horario", "08:00-20:00"),
            action=_comp("accion", "leer"),
        )],
    )
    evaluator = _build_evaluator([policy])

    assert len(evaluator.interval_tree) == 1
    assert evaluator.evaluate(_request("10:00"))["result"] == "allow"
    assert evaluator.evaluate(_request("23:00"))["result"] == "deny"


def test_dict_time_range_still_works():
    policy = Policy(
        policy_id="p1",
        description="medicos horario diurno",
        effect="allow",
        events=[Event(
            event_id="e1",
            subject=_comp("rol", "medico"),
            asset=_comp("recurso", "historia_clinica"),
            space=_comp("ubicacion", "urgencias"),
            time=_comp("rango_horario", {"range": ["08:00", "20:00"]}),
            action=_comp("accion", "leer"),
        )],
    )
    evaluator = _build_evaluator([policy])

    assert evaluator.evaluate(_request("10:00"))["result"] == "allow"


def test_categorical_time_value_still_works():
    policy = Policy(
        policy_id="p1",
        description="conexion larga",
        effect="allow",
        events=[Event(
            event_id="e1",
            subject=_comp("rol", "medico"),
            asset=_comp("recurso", "historia_clinica"),
            space=_comp("ubicacion", "urgencias"),
            time=_comp("rango_horario", "muy_larga"),
            action=_comp("accion", "leer"),
        )],
    )
    evaluator = _build_evaluator([policy])

    assert evaluator.evaluate(_request("muy_larga"))["result"] == "allow"
