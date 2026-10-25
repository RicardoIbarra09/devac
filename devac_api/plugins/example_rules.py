from typing import Optional

from devac.events import DomainEvent
from devac.models import Policy
from devac.reconfigurator import PolicyReconfigurator, ReconfigurationLevel


# nivel 1: termina la sesion del sujeto y sus allow pasan a deny
def deny_when_session_ends(policy: Policy, event: DomainEvent) -> Optional[Policy]:
    subject = (getattr(event, "subject_id", "") or "").strip().lower()
    # nada que cambiar
    if policy.effect == "deny" or not subject:
        return None
    if not any(e.subject.value == subject for e in policy.events):
        return None
    policy.effect = "deny"
    return policy


# se carga con DEVAC_RULES_MODULE=devac_api.plugins.example_rules
def register(reconfigurator: PolicyReconfigurator) -> None:
    reconfigurator.register_rule("sesion_terminada", deny_when_session_ends, ReconfigurationLevel.PARAMETRICO)
