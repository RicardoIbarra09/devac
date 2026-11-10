import pytest

import devac.evaluator as evaluator_module
from devac.evaluator import CommunityPolicyEvaluator
from devac.models import Policy
from tests.test_deny_priority import REQUEST, make_event


# lectura a mitad de una escritura: la primera vez que la escritura arma un indice de rangos
# (ya cambio la particion, todavia no los indices), se evalua la peticion como si lo hiciera otro hilo
@pytest.fixture
def read_during_write(monkeypatch):
    seen = []

    def arm(ev):
        real = evaluator_module.IntervalTree

        def tree():
            if not seen:
                seen.append(ev.evaluate(REQUEST.model_copy(deep=True)))
            return real()

        monkeypatch.setattr(evaluator_module, "IntervalTree", tree)
        return seen

    return arm


def _policies():
    return [
        Policy(policy_id="A", description="", effect="allow", events=[make_event("EA")]),
        Policy(policy_id="D", description="", effect="deny", events=[make_event("ED")]),
        Policy(policy_id="X", description="", effect="allow", events=[make_event("EX", action="borrar")]),
    ]


# run() con la particion renumerada (como la deja Louvain): mezclando la particion nueva con los
# indices viejos, la pasada de deny busca a D en c1, que ya no existe, y A concede
def test_lectura_a_mitad_de_run_no_mezcla_particion_e_indices(read_during_write, capsys):
    ev = CommunityPolicyEvaluator()
    ev.run(_policies(), {"c0": ["A"], "c1": ["D"], "c2": ["X"]})
    assert ev.evaluate(REQUEST.model_copy(deep=True))["policy_id"] == "D"

    seen = read_during_write(ev)
    ev.run(_policies(), {"c0": ["A", "D"], "c2": ["X"]})
    assert seen and (seen[0]["result"], seen[0]["policy_id"]) == ("deny", "D")
    out = ev.evaluate(REQUEST.model_copy(deep=True))
    assert (out["result"], out["policy_id"]) == ("deny", "D")
    capsys.readouterr()


# mover la deny D de c0 a c1: con la lista ya cambiada y los indices todavia viejos, la pasada de
# deny no la encuentra en c0, c1 no es candidata y A concede
def test_lectura_a_mitad_de_mover_una_deny_de_comunidad(read_during_write, capsys):
    ev = CommunityPolicyEvaluator()
    ev.run(_policies(), {"c0": ["A", "D"], "c1": ["X"]})
    assert ev.evaluate(REQUEST.model_copy(deep=True))["policy_id"] == "D"

    seen = read_during_write(ev)
    ev.move_policy_to_community("D", "c1")
    assert seen and (seen[0]["result"], seen[0]["policy_id"]) == ("deny", "D")
    out = ev.evaluate(REQUEST.model_copy(deep=True))
    assert (out["result"], out["policy_id"]) == ("deny", "D")
    assert ev.policy_community["D"] == "c1" and ev.validate_integrity() == []
    capsys.readouterr()


# lo mismo por update_policy: D estaba desactivada y al activarla toma comunidad (c1, la mas
# parecida); a mitad de la escritura un lector no debe ver a D en la lista sin sus indices
def test_lectura_a_mitad_de_activar_una_deny(read_during_write, capsys):
    ev = CommunityPolicyEvaluator()
    ev.run(_policies(), {"c0": ["A"], "c1": ["D", "X"]})
    ev.deactivate_policy("D")
    assert ev.evaluate(REQUEST.model_copy(deep=True))["policy_id"] == "A"

    seen = read_during_write(ev)
    ev.activate_policy("D")
    assert seen and seen[0]["policy_id"] == "A"  # todavia la version anterior, completa
    out = ev.evaluate(REQUEST.model_copy(deep=True))
    assert (out["result"], out["policy_id"]) == ("deny", "D")
    capsys.readouterr()


# el estado publicado no se puede reasignar por fuera ni modificar sus campos
def test_el_estado_es_de_solo_lectura():
    ev = CommunityPolicyEvaluator()
    ev.run(_policies(), {"c0": ["A", "D", "X"]})
    with pytest.raises(AttributeError):
        ev.policies = {}
    with pytest.raises(AttributeError):
        ev._state.policies = {}
