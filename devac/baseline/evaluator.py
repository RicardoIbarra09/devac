from typing import List
from devac.models import Policy, AccessRequest
from devac.matcher import EventMatcher


class TraditionalPolicyEvaluator:
    """
    Evaluador lineal O(n) — baseline de comparación para DEvAC.
    Recorre todas las políticas y eventos sin índice ni enrutamiento comunitario.
    """

    def __init__(self, policies: List[Policy]):
        self.policies = policies
        for p in self.policies:
            for e in p.events:
                if isinstance(e.subject.value, str): e.subject.value = e.subject.value.strip().lower()
                if isinstance(e.asset.value,   str): e.asset.value   = e.asset.value.strip().lower()
                if isinstance(e.space.value,   str): e.space.value   = e.space.value.strip().lower()
                if isinstance(e.time.value,    str): e.time.value    = e.time.value.strip().lower()
                if isinstance(e.action.value,  str): e.action.value  = e.action.value.strip().lower()

    def evaluate(self, request: AccessRequest) -> dict:
        request.subject.value = str(request.subject.value).strip().lower()
        request.asset.value   = str(request.asset.value).strip().lower()
        request.space.value   = str(request.space.value).strip().lower()
        request.time.value    = str(request.time.value).strip().lower()
        request.action.value  = str(request.action.value).strip().lower()

        for policy in self.policies:
            for event in policy.events:
                if EventMatcher.match_event(event, request):
                    return {
                        "result":    policy.effect,
                        "policy_id": policy.policy_id,
                        "event_id":  event.event_id,
                    }

        return {"result": "deny", "policy_id": None, "event_id": None}
