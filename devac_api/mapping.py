from typing import Optional

from devac.models import AccessRequest, AttributeComponent, Event, Policy

from devac_api.schemas import AccessRequestIn, EventOut, PolicyIn, PolicyOut, PolicySummary

# el DPL genera etiquetas en ingles y los JSON en espanol; aqui se unifican
CANONICAL_ATTRIBUTE = {
    "subject": "rol",
    "asset": "recurso",
    "space": "ubicacion",
    "time": "rango_horario",
    "action": "accion",
}
DIMENSIONS = tuple(CANONICAL_ATTRIBUTE)


def _component(dim: str, value) -> AttributeComponent:
    return AttributeComponent(attribute=CANONICAL_ATTRIBUTE[dim], value=value)


# reescribe las etiquetas de una politica que ya existe
def canonicalize_policy(policy: Policy) -> Policy:
    events = [
        Event(event_id=e.event_id, **{d: _component(d, getattr(e, d).value) for d in DIMENSIONS})
        for e in policy.events
    ]
    return policy.model_copy(update={"events": events})


# del formato http al modelo del nucleo
def policy_from_dto(dto: PolicyIn) -> Policy:
    events = [
        Event(event_id=e.event_id, **{d: _component(d, getattr(e, d)) for d in DIMENSIONS})
        for e in dto.events
    ]
    return Policy(
        policy_id=dto.policy_id,
        description=dto.description,
        events=events,
        effect=dto.effect,
        reconfigure_on=dto.reconfigure_on,
        on_deny=dto.on_deny,
    )


def request_from_dto(dto: AccessRequestIn) -> AccessRequest:
    return AccessRequest(**{d: _component(d, getattr(dto, d)) for d in DIMENSIONS})


def policy_to_dto(policy: Policy, community: Optional[str]) -> PolicyOut:
    return PolicyOut(
        policy_id=policy.policy_id,
        description=policy.description,
        effect=policy.effect,
        events=[
            EventOut(event_id=e.event_id, **{d: getattr(e, d).value for d in DIMENSIONS})
            for e in policy.events
        ],
        reconfigure_on=list(policy.reconfigure_on),
        on_deny=policy.on_deny,
        community=community,
    )


def policy_summary(policy: Policy, community: Optional[str]) -> PolicySummary:
    return PolicySummary(
        policy_id=policy.policy_id,
        effect=policy.effect,
        n_events=len(policy.events),
        community=community,
    )
