import pytest

from devac import compile_dpl, compile_dpl_to_policies, DPLSyntaxError
from devac.matcher import EventMatcher
from devac.models import AccessRequest, AttributeComponent


PAPER_EXAMPLE = """
POLICY P1 EFFECT ALLOW {
    EVENT E1 WHERE (SUBJECT  = Teacher,
        RESOURCE = Grades,
        LOCATION = Campus,
        TIME     = Day,
        ACTION   = Modify
    );
    EVENT E2 WHERE (SUBJECT  = Teacher,
        RESOURCE = CourseMaterial,
        LOCATION = *,
        TIME     = *,
        ACTION   = Read
    );
    EVENT E3 WHERE (SUBJECT  IN (Student, Teacher),
        RESOURCE = Syllabus,
        LOCATION = *,
        TIME     IN (Morning, Afternoon),
        ACTION   = Read
    );
    EVENT E4 WHERE (SUBJECT  = Administrator,
        RESOURCE = *,
        LOCATION = Campus,
        TIME     RANGE (08:00, 18:00),
        ACTION   = *
    );
}
"""


def _req(subject, asset, space, time, action):
    return AccessRequest(
        subject=AttributeComponent(attribute="subject", value=subject),
        asset=AttributeComponent(attribute="asset", value=asset),
        space=AttributeComponent(attribute="space", value=space),
        time=AttributeComponent(attribute="time", value=time),
        action=AttributeComponent(attribute="action", value=action),
    )


def test_compiles_paper_example_to_one_policy_four_events():
    policies = compile_dpl_to_policies(PAPER_EXAMPLE)
    assert len(policies) == 1
    assert policies[0].policy_id == "P1"
    assert policies[0].effect == "allow"
    assert len(policies[0].events) == 4


def test_exact_match_event_e1():
    p1 = compile_dpl_to_policies(PAPER_EXAMPLE)[0]
    req = _req("Teacher", "Grades", "Campus", "Day", "Modify")
    assert EventMatcher.match_event(p1.events[0], req)


def test_wildcard_event_e2():
    p1 = compile_dpl_to_policies(PAPER_EXAMPLE)[0]
    req = _req("Teacher", "CourseMaterial", "AnyCampus", "AnyTime", "Read")
    assert EventMatcher.match_event(p1.events[1], req)


def test_set_membership_event_e3():
    p1 = compile_dpl_to_policies(PAPER_EXAMPLE)[0]
    req = _req("Student", "Syllabus", "AnyCampus", "Morning", "Read")
    assert EventMatcher.match_event(p1.events[2], req)
    assert not EventMatcher.match_event(p1.events[2], _req("Visitor", "Syllabus", "X", "Morning", "Read"))


def test_range_event_e4():
    p1 = compile_dpl_to_policies(PAPER_EXAMPLE)[0]
    inside = _req("Administrator", "AnyResource", "Campus", "10:00", "Delete")
    outside = _req("Administrator", "AnyResource", "Campus", "23:00", "Delete")
    assert EventMatcher.match_event(p1.events[3], inside)
    assert not EventMatcher.match_event(p1.events[3], outside)


def test_reconfigure_on_and_on_deny_extension():
    text = """
    POLICY ALM-01 EFFECT ALLOW RECONFIGURE_ON (almacen_eliminado, rol_revocado) ON_DENY ESCALATE {
        EVENT E1 WHERE (SUBJECT = investigador, RESOURCE = almacen_temp,
            LOCATION = zona_a, TIME = *, ACTION = leer);
    }
    """
    policy = compile_dpl_to_policies(text)[0]
    assert policy.reconfigure_on == ["almacen_eliminado", "rol_revocado"]
    assert policy.on_deny == "escalate"


def test_policy_without_reconfigure_on_defaults_to_empty_list():
    p1 = compile_dpl_to_policies(PAPER_EXAMPLE)[0]
    assert p1.reconfigure_on == []
    assert p1.on_deny is None


def test_multiple_policies_in_one_document():
    text = """
    POLICY A EFFECT ALLOW { EVENT EA WHERE (SUBJECT = x, RESOURCE = y, LOCATION = z, TIME = *, ACTION = leer); }
    POLICY B EFFECT DENY  { EVENT EB WHERE (SUBJECT = x, RESOURCE = y, LOCATION = z, TIME = *, ACTION = escribir); }
    """
    compiled = compile_dpl(text)
    assert [p["policy_id"] for p in compiled] == ["A", "B"]
    assert compiled[1]["effect"] == "deny"


def test_missing_dimension_raises_syntax_error():
    text = """
    POLICY A EFFECT ALLOW {
        EVENT E1 WHERE (SUBJECT = x, RESOURCE = y, LOCATION = z, ACTION = leer);
    }
    """
    with pytest.raises(DPLSyntaxError):
        compile_dpl(text)


def test_in_requires_at_least_two_values():
    text = """
    POLICY A EFFECT ALLOW {
        EVENT E1 WHERE (SUBJECT IN (soloUno), RESOURCE = y, LOCATION = z, TIME = *, ACTION = leer);
    }
    """
    with pytest.raises(DPLSyntaxError):
        compile_dpl(text)


def test_invalid_effect_raises_syntax_error():
    text = "POLICY A EFFECT MAYBE { EVENT E1 WHERE (SUBJECT = x, RESOURCE = y, LOCATION = z, TIME = *, ACTION = leer); }"
    with pytest.raises(DPLSyntaxError):
        compile_dpl(text)
