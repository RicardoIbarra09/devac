from devac.models import Policy, Event, AttributeComponent, AccessRequest
from devac.evaluator import CommunityPolicyEvaluator
from devac.baseline.evaluator import TraditionalPolicyEvaluator


def comp(attr, val):
    return AttributeComponent(attribute=attr, value=val)


def make_event(eid, subject="medico", asset="expediente_x", space="piso1",
               time="*", action="leer"):
    return Event(
        event_id=eid,
        subject=comp("subject", subject),
        asset=comp("asset", asset),
        space=comp("space", space),
        time=comp("time", time),
        action=comp("action", action),
    )


REQUEST = AccessRequest(
    subject=comp("subject", "medico"),
    asset=comp("asset", "expediente_x"),
    space=comp("space", "piso1"),
    time=comp("time", "10:00"),
    action=comp("action", "leer"),
)


def test_deny_gana_entre_comunidades_distintas_devac():
    p_allow = Policy(policy_id="P_ALLOW", description="permite", effect="allow",
                      events=[make_event("E_ALLOW")])
    p_deny = Policy(policy_id="P_DENY", description="niega", effect="deny",
                     events=[make_event("E_DENY")])

    # separadas a mano: el audit no las compararia
    pbc = {"comm_allow": ["P_ALLOW"], "comm_deny": ["P_DENY"]}
    ce = CommunityPolicyEvaluator()
    ce.run([p_allow, p_deny], pbc)

    result = ce.evaluate(REQUEST.model_copy(deep=True))
    assert result["result"] == "deny"
    assert result["policy_id"] == "P_DENY"
    assert result["steps"] > 0


def test_deny_gana_entre_comunidades_distintas_lineal_sin_importar_orden():
    p_allow = Policy(policy_id="P_ALLOW", description="permite", effect="allow",
                      events=[make_event("E_ALLOW")])
    p_deny = Policy(policy_id="P_DENY", description="niega", effect="deny",
                     events=[make_event("E_DENY")])

    # el orden del json no debe importar
    for order in ([p_allow, p_deny], [p_deny, p_allow]):
        te = TraditionalPolicyEvaluator(list(order))
        result = te.evaluate(REQUEST.model_copy(deep=True))
        assert result["result"] == "deny"
        assert result["policy_id"] == "P_DENY"
        assert "steps" in result and result["steps"] > 0
        assert "eval_time" in result


def test_deny_gana_dentro_de_la_misma_comunidad_devac():
    p_allow = Policy(policy_id="P_ALLOW", description="permite", effect="allow",
                      events=[make_event("E_ALLOW")])
    p_deny = Policy(policy_id="P_DENY", description="niega", effect="deny",
                     events=[make_event("E_DENY")])

    pbc = {"comm_1": ["P_ALLOW", "P_DENY"]}  # allow primero en la lista
    ce = CommunityPolicyEvaluator()
    ce.run([p_allow, p_deny], pbc)

    result = ce.evaluate(REQUEST.model_copy(deep=True))
    assert result["result"] == "deny"
    assert result["policy_id"] == "P_DENY"


def test_sin_conflicto_allow_normal_no_se_ve_afectado():
    p_allow = Policy(policy_id="P_ALLOW", description="permite", effect="allow",
                      events=[make_event("E_ALLOW")])
    p_deny_otra_cosa = Policy(
        policy_id="P_DENY_OTRO", description="niega otra cosa", effect="deny",
        events=[make_event("E_DENY_OTRO", subject="auditor", asset="nomina",
                            space="piso3", action="escribir")],
    )

    pbc = {"comm_allow": ["P_ALLOW"], "comm_deny": ["P_DENY_OTRO"]}
    ce = CommunityPolicyEvaluator()
    ce.run([p_allow, p_deny_otra_cosa], pbc)

    result = ce.evaluate(REQUEST.model_copy(deep=True))
    assert result["result"] == "allow"
    assert result["policy_id"] == "P_ALLOW"

    te = TraditionalPolicyEvaluator([p_allow, p_deny_otra_cosa])
    result_lineal = te.evaluate(REQUEST.model_copy(deep=True))
    assert result_lineal["result"] == "allow"
    assert result_lineal["policy_id"] == "P_ALLOW"
