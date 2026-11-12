from dataclasses import replace

import pytest

from devac.evaluator import CommunityPolicyEvaluator
from devac.models import Policy
from tests.test_deny_priority import REQUEST, make_event


# un dict que, la primera vez que evaluate() lee una politica, corre una escritura a mitad del
# recorrido (como si otro hilo la hiciera en ese momento)
class _WriteDuringRead(dict):
    def __init__(self, data, write):
        super().__init__(data)
        self.write = write

    def get(self, key, default=None):
        write, self.write = self.write, None
        if write:
            write()
        return super().get(key, default)


# A no aplica a la peticion; B si. Si quitar A modifica en el lugar la lista que se esta
# recorriendo, el lector se salta B
@pytest.mark.parametrize("effect_a, effect_b, c_applies, expected", [
    ("allow", "allow", False, ("allow", "B")),  # sin la correccion: deny por defecto, el caso de E5
    ("deny", "deny", True, ("deny", "B")),      # sin la correccion: se salta el deny y C concede
])
def test_quitar_una_politica_a_mitad_de_evaluate_no_salta_otra(effect_a, effect_b, c_applies, expected):
    policies = [
        Policy(policy_id="A", description="", effect=effect_a, events=[make_event("EA", action="borrar")]),
        Policy(policy_id="B", description="", effect=effect_b, events=[make_event("EB")]),
        Policy(policy_id="C", description="", effect="allow",
               events=[make_event("EC", action="leer" if c_applies else "borrar")]),
    ]
    ev = CommunityPolicyEvaluator()
    ev.run(policies, {"c0": ["A", "B", "C"]})
    assert (ev.evaluate(REQUEST.model_copy(deep=True))["result"]) == expected[0]

    ev._state = replace(ev._state, policies=_WriteDuringRead(ev.policies, lambda: ev.remove_policy("A")))
    out = ev.evaluate(REQUEST.model_copy(deep=True))
    assert (out["result"], out["policy_id"]) == expected
    assert "A" not in ev.policies and ev.policies_by_community["c0"] == ["B", "C"]
    assert ev.validate_integrity() == []


# ninguna escritura modifica en el lugar lo que evaluate() pudo haber tomado
def test_las_escrituras_no_modifican_estructuras_publicadas(capsys):
    policies = [Policy(policy_id=p, description="", effect="allow", events=[make_event("E" + p)])
                for p in "ABCD"]
    ev = CommunityPolicyEvaluator()
    ev.run(policies, {"c0": ["A", "B"], "c1": ["C", "D"]})

    def snapshot():
        return {
            "policies": (ev.policies, dict(ev.policies)),
            "policy_community": (ev.policy_community, dict(ev.policy_community)),
            "excluded": (ev.excluded, dict(ev.excluded)),
            "pbc": (ev.policies_by_community, {c: (l, list(l)) for c, l in ev.policies_by_community.items()}),
            "events_by_comm": (ev.events_by_comm, {c: (l, list(l)) for c, l in ev.events_by_comm.items()}),
            "freq": (ev.freq, dict(ev.freq)),
            "value_to_comms": (ev.value_to_comms,
                               {a: {v: (s, dict(s)) for v, s in idx.items()} for a, idx in ev.value_to_comms.items()}),
        }

    def unchanged(before):
        for name in ("policies", "policy_community", "excluded", "freq"):
            obj, copy = before[name]
            assert obj == copy, name
        for name in ("pbc", "events_by_comm"):
            obj, lists = before[name]
            assert set(obj) == set(lists), name
            for c, (lst, copy) in lists.items():
                assert lst == copy, (name, c)
        for attr, idx in before["value_to_comms"][1].items():
            for v, (s, copy) in idx.items():
                assert s == copy, (attr, v)

    writes = [
        lambda: ev.remove_policy("A"),
        lambda: ev.add_policy(Policy(policy_id="E", description="", effect="deny", events=[make_event("EE")])),
        lambda: ev.update_policy(Policy(policy_id="C", description="", effect="allow",
                                        events=[make_event("EC2", action="escribir")])),
        lambda: ev.deactivate_policy("D"),
        lambda: ev.activate_policy("D"),
        lambda: ev.move_policy_to_community("B", "c1"),
        lambda: ev.assign_policy_to_community("B", "c9"),
    ]
    for write in writes:
        before = snapshot()
        write()
        unchanged(before)
        assert ev.validate_integrity() == []
    capsys.readouterr()


# la auditoria: liberar exclusiones tampoco modifica el dict que lee evaluate()
def test_liberar_exclusiones_no_modifica_excluded(capsys):
    policies = [Policy(policy_id=p, description="", effect="allow", events=[make_event("E" + p)])
                for p in "AB"]
    ev = CommunityPolicyEvaluator()
    ev.run(policies, {"c0": ["A", "B"]}, excluded={"EB": ("B", "A")})
    excluded = ev.excluded
    ev.remove_policy("A")
    assert excluded == {"EB": ("B", "A")} and ev.excluded == {}
    out = ev.evaluate(REQUEST.model_copy(deep=True))
    assert (out["result"], out["policy_id"]) == ("allow", "B")
    capsys.readouterr()
