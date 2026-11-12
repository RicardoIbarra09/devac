import random

import pytest

from devac.evaluator import CommunityPolicyEvaluator
from devac.models import AttributeComponent, Event, Policy

ROLES = ["medico", "enfermera", "admin"]
ASSETS = ["historia", "receta", "agenda"]
SPACES = ["urgencias", "piso1", "piso2"]
ACTIONS = ["leer", "escribir", "borrar"]
TIMES = [["08:00", "14:00"], ["13:00", "20:00"], ["22:00", "02:00"]]  # el ultimo cruza medianoche


def _value(rng, vocab):
    r = rng.random()
    if r < 0.15:
        return "*"
    if r < 0.3:
        return {"in": rng.sample(vocab, 2)}
    return rng.choice(vocab)


def _time(rng):
    r = rng.random()
    if r < 0.4:
        return {"range": list(rng.choice(TIMES))}
    if r < 0.6:
        return "*"
    return rng.choice(["manana", "tarde"])


class _Gen:
    def __init__(self, rng):
        self.rng = rng
        self.n_events = 0
        self.n_policies = 0

    def events(self):
        rng, out = self.rng, []
        for _ in range(rng.randint(1, 3)):
            self.n_events += 1
            out.append(Event(
                event_id=f"E{self.n_events}",
                subject=AttributeComponent(attribute="subject", value=_value(rng, ROLES)),
                asset=AttributeComponent(attribute="asset", value=_value(rng, ASSETS)),
                space=AttributeComponent(attribute="space", value=_value(rng, SPACES)),
                time=AttributeComponent(attribute="time", value=_time(rng)),
                action=AttributeComponent(attribute="action", value=_value(rng, ACTIONS)),
            ))
        return out

    def policy(self):
        self.n_policies += 1
        return Policy(policy_id=f"P{self.n_policies}", description="",
                      effect=self.rng.choice(["allow", "deny"]), events=self.events())


# lo que se compara: todos los indices que arma _rebuild_all_indices
def _indices(st):
    return {
        "value_to_comms": st.value_to_comms,
        "value_to_comms_deny": st.value_to_comms_deny,
        "freq": st.freq,
        "events_by_comm": {c: [e.event_id for e in evs] for c, evs in st.events_by_comm.items()},
        "event_policy_effect": st.event_policy_effect,
        "contributions": st.contributions,
        "interval_tree": st.interval_tree.intervals(),
        "interval_tree_deny": st.interval_tree_deny.intervals(),
    }


# muchos cambios al azar; despues de cada uno los indices incrementales son identicos a los de
# reconstruir todo desde cero sobre el mismo estado
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_indices_incrementales_iguales_a_reconstruir_todo(seed, capsys):
    rng = random.Random(seed)
    gen = _Gen(rng)
    policies = [gen.policy() for _ in range(30)]
    partition = {f"c{i}": [] for i in range(4)}
    for p in policies:
        partition[rng.choice(list(partition))].append(p.policy_id)
    # algunos eventos excluidos por la auditoria, para que tambien se liberen
    excluded = {}
    for owner, cover in zip(policies[::5], policies[1::5]):
        excluded[owner.events[0].event_id] = (owner.policy_id, cover.policy_id)

    ev = CommunityPolicyEvaluator()
    ev.run(policies, partition, excluded=excluded)
    assert _indices(ev._state) == _indices(ev._rebuild_all_indices(ev._state))

    ops = ["add", "remove", "update", "update", "deactivate", "activate", "move", "assign"]
    for step in range(300):
        op = rng.choice(ops)
        pids = sorted(ev.policies)
        if op == "add" or not pids:
            ev.add_policy(gen.policy())
        elif op == "remove":
            ev.remove_policy(rng.choice(pids))
        elif op == "update":
            old = ev.policies[rng.choice(pids)]
            r = rng.random()
            if r < 0.15:
                events = []  # se vacia
            elif r < 0.5:
                events = list(old.events)  # mismos eventos, quiza otro efecto
            else:
                events = gen.events()
            ev.update_policy(old.model_copy(update={"events": events, "effect": rng.choice(["allow", "deny"])}))
        elif op == "deactivate":
            ev.deactivate_policy(rng.choice(pids))
        elif op == "activate":
            ev.activate_policy(rng.choice(pids))
        elif op == "move":
            target = rng.choice(list(ev.policies_by_community) + [f"n{step}"])
            ev.move_policy_to_community(rng.choice(pids), target)
        else:
            # con o sin comunidad: si ya tenia una, sale de ella (como move)
            pid = rng.choice(pids)
            ev.assign_policy_to_community(pid, rng.choice(list(ev.policies_by_community)))
            assert sum(pid in lst for lst in ev.policies_by_community.values()) == 1
            assert pid in ev.policies_by_community[ev.policy_community[pid]]

        assert _indices(ev._state) == _indices(ev._rebuild_all_indices(ev._state)), (step, op)
        assert ev.validate_integrity() == []
    capsys.readouterr()


# sin rangos horarios en la comunidad que cambia, los arboles publicados se reutilizan tal cual
def test_arboles_se_reutilizan_si_no_hay_rangos(capsys):
    def event(eid, time):
        return Event(event_id=eid, **{
            dim: AttributeComponent(attribute=dim, value=val)
            for dim, val in [("subject", "medico"), ("asset", "historia"), ("space", "piso1"),
                             ("time", time), ("action", "leer")]
        })

    ev = CommunityPolicyEvaluator()
    ev.run([Policy(policy_id="R", description="", effect="deny", events=[event("ER", "08:00-14:00")]),
            Policy(policy_id="S", description="", effect="allow", events=[event("ES", "*")])],
           {"c0": ["R"], "c1": ["S"]})
    tree, tree_deny = ev.interval_tree, ev.interval_tree_deny

    ev.update_policy(Policy(policy_id="S", description="", effect="deny", events=[event("ES2", "manana")]))
    assert ev.interval_tree is tree and ev.interval_tree_deny is tree_deny

    ev.update_policy(Policy(policy_id="S", description="", effect="allow", events=[event("ES3", "09:00-10:00")]))
    assert ev.interval_tree is not tree and ev.interval_tree_deny is tree_deny  # S no tiene rangos deny
    assert ev.interval_tree.intervals() == [(480, 840, "c0", "ER"), (540, 600, "c1", "ES3")]
    capsys.readouterr()
