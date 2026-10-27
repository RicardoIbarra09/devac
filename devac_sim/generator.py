import copy
import random
from dataclasses import asdict, dataclass
from typing import Dict, List, Tuple

from devac.events import INFRASTRUCTURE_EVENTS
from devac.models import Policy

from devac_sim.arrivals import poisson_times, arrival_times
from devac_sim.trace import (
    ACCESS, DIMENSIONS, DOMAIN_EVENT, POLICY_ADD, POLICY_DELETE, POLICY_UPDATE,
)

UNSEEN = "valor_desconocido"  # no aparece en ninguna politica
MUTATION_WEIGHTS = {POLICY_ADD: 0.4, POLICY_UPDATE: 0.4, POLICY_DELETE: 0.2}


@dataclass
class TraceParams:
    window: float = 60.0
    rate: float = 100.0
    arrivals: str = "poisson"
    mutation_rate: float = 0.0
    event_rate: float = 0.0
    match_fraction: float = 0.5
    seed: int = 1
    peak_factor: float = 3.0
    burst_factor: float = 4.0
    burst_fraction: float = 0.2
    burst_len: float = 1.0


def _fmt(minutes: int) -> str:
    return f"{minutes // 60 % 24:02d}:{minutes % 60:02d}"


def _hm(text: str) -> int:
    h, m = text.strip().split(":")
    return int(h) * 60 + int(m)


def _random_range(rng: random.Random) -> Dict[str, list]:
    start = rng.randrange(0, 1440, 15)
    length = rng.randrange(60, 721, 15)
    return {"range": [_fmt(start), _fmt(start + length)]}  # puede cruzar medianoche


class _PolicyState:
    def __init__(self, policies: List[dict]):
        self.policies: Dict[str, dict] = {p["policy_id"]: p for p in policies}
        self.next_id = 1
        self.vocab = self._vocab(policies)

    @staticmethod
    def _vocab(policies: List[dict]) -> Dict[str, List[str]]:
        vocab = {d: set() for d in DIMENSIONS}
        for p in policies:
            for e in p["events"]:
                for d in DIMENSIONS:
                    v = e[d]["value"]
                    if isinstance(v, dict) and "in" in v:
                        vocab[d].update(str(x).strip().lower() for x in v["in"])
                    elif isinstance(v, str) and v.strip() != "*":
                        vocab[d].add(v.strip().lower())
        return {d: sorted(vals) for d, vals in vocab.items()}

    def new_policy_id(self) -> str:
        while f"sim-p{self.next_id:05d}" in self.policies:
            self.next_id += 1
        pid = f"sim-p{self.next_id:05d}"
        self.next_id += 1
        return pid

    def reconfigure_types(self) -> List[str]:
        return sorted({t for p in self.policies.values() for t in p.get("reconfigure_on", [])})


def _matching_request(rng: random.Random, state: _PolicyState) -> Dict[str, str]:
    policy = state.policies[rng.choice(list(state.policies))]
    event = rng.choice(policy["events"])
    request = {}
    for d in DIMENSIONS:
        v = event[d]["value"]
        if isinstance(v, dict) and "range" in v:
            lo, hi = _hm(v["range"][0]), _hm(v["range"][1])
            span = (hi - lo) % 1440 + 1  # minutos dentro del rango, cruce de medianoche incluido
            request[d] = _fmt(lo + rng.randrange(span))
        elif isinstance(v, dict) and "in" in v:
            request[d] = str(rng.choice(sorted(v["in"]))).strip().lower()
        elif str(v).strip() == "*":
            request[d] = _random_value(rng, state, d, unseen_p=0.0)
        else:
            request[d] = str(v).strip().lower()
    return request


def _random_value(rng: random.Random, state: _PolicyState, dim: str, unseen_p: float) -> str:
    if dim == "time":
        return _fmt(rng.randrange(1440))
    pool = state.vocab[dim]
    if not pool or rng.random() < unseen_p:
        return UNSEEN
    return rng.choice(pool)


def _random_request(rng: random.Random, state: _PolicyState) -> Dict[str, str]:
    return {d: _random_value(rng, state, d, unseen_p=0.15) for d in DIMENSIONS}


def _mutate(policy: dict, rng: random.Random) -> dict:
    changed = copy.deepcopy(policy)
    if rng.random() < 0.5:
        changed["effect"] = "deny" if changed["effect"] == "allow" else "allow"
    else:
        event = rng.choice(changed["events"])
        event["time"]["value"] = _random_range(rng)
    return changed


def _policy_mutation(rng: random.Random, state: _PolicyState) -> dict:
    kinds, weights = zip(*MUTATION_WEIGHTS.items())
    kind = rng.choices(kinds, weights)[0]
    if kind == POLICY_DELETE and len(state.policies) <= 1:
        kind = POLICY_ADD  # nunca se deja el sistema sin politicas

    if kind == POLICY_DELETE:
        pid = rng.choice(list(state.policies))
        del state.policies[pid]
        return {"kind": kind, "policy_id": pid}

    if kind == POLICY_UPDATE:
        pid = rng.choice(list(state.policies))
        state.policies[pid] = _mutate(state.policies[pid], rng)
        return {"kind": kind, "policy": copy.deepcopy(state.policies[pid])}

    template = state.policies[rng.choice(list(state.policies))]
    policy = _mutate(template, rng)
    pid = state.new_policy_id()
    policy["policy_id"] = pid
    policy["description"] = f"copia de {template['policy_id']}"
    for k, event in enumerate(policy["events"], start=1):
        event["event_id"] = f"{pid}-e{k}"
    state.policies[pid] = policy
    return {"kind": kind, "policy": copy.deepcopy(policy)}


# de donde sale cada campo obligatorio de un evento de dominio
def _field_value(name: str, rng: random.Random, state: _PolicyState) -> str:
    if name in ("subject_id", "rol", "rol_anterior", "rol_nuevo"):
        return _random_value(rng, state, "subject", unseen_p=0.0)
    if name == "asset_id":
        return _random_value(rng, state, "asset", unseen_p=0.0)
    if name == "space":
        return _random_value(rng, state, "space", unseen_p=0.0)
    if name == "policy_id":
        return rng.choice(list(state.policies))
    return f"{name}-{rng.randrange(1000)}"


def _domain_event(rng: random.Random, state: _PolicyState) -> dict:
    event_type = rng.choice(state.reconfigure_types())
    event_cls = INFRASTRUCTURE_EVENTS.get(event_type)
    event: dict = {"event_type": event_type, "source": "sim"}
    if event_cls is None:
        # tipo propio del dominio: sus datos van en payload
        event["payload"] = {"subject_id": _field_value("subject_id", rng, state)}
        return {"kind": DOMAIN_EVENT, "event": event}
    for name, field in event_cls.model_fields.items():
        if field.is_required():
            event[name] = _field_value(name, rng, state)
    return {"kind": DOMAIN_EVENT, "event": event}


def generate_trace(policies: List[dict], params: TraceParams) -> Tuple[dict, List[dict]]:
    if not 0 <= params.match_fraction <= 1:
        raise ValueError("match_fraction debe estar entre 0 y 1")
    normalized = [Policy.model_validate(p).model_dump() for p in policies]
    if not normalized:
        raise ValueError("se necesita al menos una política inicial")
    state = _PolicyState(normalized)

    # un generador por flujo: cambiar una tasa no altera los otros flujos
    access_rng = random.Random(f"{params.seed}-access")
    content_rng = random.Random(f"{params.seed}-content")
    control_rng = random.Random(f"{params.seed}-control")

    timeline: List[Tuple[float, int, str]] = []  # (t, prioridad, tipo); control antes que acceso si empatan
    timeline += [(t, 1, ACCESS) for t in arrival_times(
        params.arrivals, params.rate, params.window, access_rng,
        peak_factor=params.peak_factor, burst_factor=params.burst_factor,
        burst_fraction=params.burst_fraction, burst_len=params.burst_len,
    )]
    if params.mutation_rate > 0:
        timeline += [(t, 0, "mutation") for t in poisson_times(control_rng, params.mutation_rate, params.window)]
    if params.event_rate > 0:
        timeline += [(t, 0, DOMAIN_EVENT) for t in poisson_times(control_rng, params.event_rate, params.window)]
    timeline.sort()

    records = []
    skipped_events = 0
    for t, _, kind in timeline:
        if kind == ACCESS:
            want_match = content_rng.random() < params.match_fraction
            request = _matching_request(content_rng, state) if want_match else _random_request(content_rng, state)
            record = {"kind": ACCESS, "request": request}
        elif kind == "mutation":
            record = _policy_mutation(control_rng, state)
        else:
            # ninguna politica declara reconfigure_on: el evento no tendria efecto
            if not state.reconfigure_types():
                skipped_events += 1
                continue
            record = _domain_event(control_rng, state)
        records.append({"t": round(t, 6), **record})

    meta = {
        "generator": "devac-sim",
        "params": asdict(params),
        "initial_policies": len(normalized),
        "records": len(records),
        "access": sum(1 for r in records if r["kind"] == ACCESS),
        "skipped_domain_events": skipped_events,
    }
    return meta, records
