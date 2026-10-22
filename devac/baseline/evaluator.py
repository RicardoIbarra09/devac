from typing import List
from devac.models import Policy, AccessRequest
from devac.matcher import EventMatcher
import time


class TraditionalPolicyEvaluator:
    """
    Evaluador lineal O(n) — baseline de comparación para DEvAC.
    Recorre todas las políticas y eventos sin índice ni enrutamiento comunitario.
    """

    def __init__(self, policies: List[Policy]):
        # "deny gana": se ordenan las politicas deny antes que las allow
        # UNA SOLA VEZ aqui -- "primer match gana" sobre esta lista ya
        # implementa "deny gana" sin tocar el costo de evaluate() ni su
        # algoritmo. sorted() es estable, asi que el orden relativo dentro
        # de cada efecto (el orden original de entrada) no cambia.
        self.policies = sorted(policies, key=lambda p: 0 if p.effect == "deny" else 1)
        for p in self.policies:
            for e in p.events:
                if isinstance(e.subject.value, str): e.subject.value = e.subject.value.strip().lower()
                if isinstance(e.asset.value,   str): e.asset.value   = e.asset.value.strip().lower()
                if isinstance(e.space.value,   str): e.space.value   = e.space.value.strip().lower()
                if isinstance(e.time.value,    str): e.time.value    = e.time.value.strip().lower()
                if isinstance(e.action.value,  str): e.action.value  = e.action.value.strip().lower()

    def evaluate(self, request: AccessRequest) -> dict:
        start = time.time()

        request.subject.value = str(request.subject.value).strip().lower()
        request.asset.value   = str(request.asset.value).strip().lower()
        request.space.value   = str(request.space.value).strip().lower()
        request.time.value    = str(request.time.value).strip().lower()
        request.action.value  = str(request.action.value).strip().lower()

        steps = 0
        for policy in self.policies:
            for event in policy.events:
                steps += 1
                if EventMatcher.match_event(event, request):
                    eval_time = time.time() - start
                    return {
                        "result":    policy.effect,
                        "policy_id": policy.policy_id,
                        "event_id":  event.event_id,
                        "eval_time": eval_time,
                        "steps":     steps,
                    }

        eval_time = time.time() - start
        return {"result": "deny", "policy_id": None, "event_id": None, "eval_time": eval_time, "steps": steps}
