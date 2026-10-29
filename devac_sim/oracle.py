import importlib
import logging
from copy import deepcopy
from typing import Dict, List, Optional, Tuple

from devac.events import DomainEvent, PoliticaActualizada, PoliticaAgregada, PoliticaEliminada
from devac.models import Policy

from devac_sim.trace import (
    DIMENSIONS, DOMAIN_EVENT, POLICY_ADD, POLICY_DELETE, POLICY_UPDATE, build_domain_event,
)

log = logging.getLogger(__name__)

MAX_CASCADE_DEPTH = 3  # el mismo limite que PolicyReconfigurator

# (efecto, [componente por dimension]) por cada evento de la politica
CompiledPolicy = Tuple[str, List[list]]


def _minutes(text: str) -> Optional[int]:
    hh, sep, mm = str(text).strip().partition(":")
    if not sep or not (hh.isascii() and hh.isdigit() and mm.isascii() and mm.isdigit()):
        return None
    h, m = int(hh), int(mm)
    return h * 60 + m if h < 24 and m < 60 else None


# no usa EventMatcher a proposito: es la referencia contra la que se compara
def _compile_value(value) -> tuple:
    if isinstance(value, dict):
        if "in" in value:
            return ("in", {str(v).strip().lower() for v in value["in"]})
        if "range" in value and len(value["range"]) == 2:
            return ("range", (_minutes(value["range"][0]), _minutes(value["range"][1])))
        return ("never", None)
    v = str(value).strip().lower()
    return ("any", None) if v == "*" else ("eq", v)


def _covers(component: tuple, value: str) -> bool:
    kind, data = component
    if kind == "any":
        return True
    if kind == "eq":
        return value == data
    if kind == "in":
        return value in data
    if kind == "range":
        lo, hi = data
        point = _minutes(value)
        if lo is None or hi is None or point is None:
            return False
        return lo <= point <= hi if lo <= hi else point >= lo or point <= hi
    return False


def _compile(policy: Policy) -> CompiledPolicy:
    events = policy.events if policy.active else []  # inactiva: no aplica
    return policy.effect, [[_compile_value(getattr(e, d).value) for d in DIMENSIONS] for e in events]


# el evaluador pasa los valores a minusculas al indexar; las reglas ven eso mismo
def _normalized(policy: Policy) -> Policy:
    policy = deepcopy(policy)
    for e in policy.events:
        for d in DIMENSIONS:
            component = getattr(e, d)
            if isinstance(component.value, str):
                component.value = component.value.strip().lower()
    return policy


class _RuleCollector:
    def __init__(self):
        self.rules: Dict[str, list] = {}

    # misma firma que PolicyReconfigurator.register_rule
    def register_rule(self, event_type, rule_fn, level) -> None:
        self.rules.setdefault(event_type, []).append(rule_fn)


def load_rules(module_name: Optional[str]) -> Dict[str, list]:
    collector = _RuleCollector()
    if module_name:
        importlib.import_module(module_name).register(collector)
    return collector.rules


class Oracle:
    def __init__(self, policies: List[Policy], rules: Optional[Dict[str, list]] = None):
        self._rules = rules or {}
        self._policies: Dict[str, Policy] = {}
        self._compiled: Dict[str, CompiledPolicy] = {}
        for policy in policies:
            self._set(_normalized(policy))
        self._snapshots: List[Dict[str, CompiledPolicy]] = [dict(self._compiled)]
        self.errors: List[str] = []
        self.reconfigurations = 0

    @property
    def version(self) -> int:
        return len(self._snapshots) - 1

    def policy(self, policy_id: str) -> Optional[Policy]:
        return self._policies.get(policy_id)

    def _set(self, policy: Policy) -> None:
        self._policies[policy.policy_id] = policy
        self._compiled[policy.policy_id] = _compile(policy)

    def _drop(self, policy_id: str) -> None:
        self._policies.pop(policy_id, None)
        self._compiled.pop(policy_id, None)

    # una linea de control de la traza = una version nueva
    def apply(self, record: dict) -> int:
        kind = record["kind"]
        if kind == POLICY_ADD:
            policy = Policy.model_validate(record["policy"])
            if policy.policy_id in self._policies:
                self.errors.append(f"t={record['t']}: alta de '{policy.policy_id}' que ya existe")
            else:
                self._set(_normalized(policy))
                self._publish(PoliticaAgregada(policy_id=policy.policy_id, source="api"))
        elif kind == POLICY_UPDATE:
            policy = Policy.model_validate(record["policy"])
            if policy.policy_id not in self._policies:
                self.errors.append(f"t={record['t']}: cambio de '{policy.policy_id}' que no existe")
            else:
                self._set(_normalized(policy))
                self._publish(PoliticaActualizada(policy_id=policy.policy_id, source="api"))
        elif kind == POLICY_DELETE:
            policy_id = record["policy_id"]
            if policy_id not in self._policies:
                self.errors.append(f"t={record['t']}: baja de '{policy_id}' que no existe")
            else:
                self._drop(policy_id)
                self._publish(PoliticaEliminada(policy_id=policy_id, source="api"))
        elif kind == DOMAIN_EVENT:
            self._publish(build_domain_event(record["event"]))
        else:
            raise ValueError(f"{kind} no es un registro de control")
        self._snapshots.append(dict(self._compiled))
        return self.version

    # reproduce PolicyReconfigurator.handle, cascada incluida
    def _publish(self, event: DomainEvent) -> None:
        rules = self._rules.get(event.event_type, [])
        if not rules:
            return
        depth = int(event.payload.get("cascade_depth", 0))
        affected = [pid for pid, p in self._policies.items() if event.event_type in p.reconfigure_on]

        changed: List[str] = []
        for pid in affected:
            working = deepcopy(self._policies[pid])
            modified = False
            try:
                for rule in rules:
                    result = rule(working, event)
                    if result is not None:
                        working, modified = result, True
            except Exception:
                log.exception("regla falló en el oráculo para '%s'", pid)
                continue
            if modified:
                self._set(_normalized(working))
                changed.append(pid)
                self.reconfigurations += 1

        if changed and depth < MAX_CASCADE_DEPTH:
            self._publish(PoliticaActualizada(
                policy_id=changed[0],
                source="reconfigurator",
                payload={"cascade_depth": depth + 1, "policy_ids": changed},
            ))

    # deny gana; sin cobertura tambien es deny
    def decide(self, request: Dict[str, str], version: Optional[int] = None) -> Tuple[str, bool]:
        compiled = self._snapshots[self.version if version is None else version]
        values = [str(request[d]).strip().lower() for d in DIMENSIONS]
        covered = False
        for effect, events in compiled.values():
            for components in events:
                if all(_covers(c, v) for c, v in zip(components, values)):
                    if effect == "deny":
                        return "deny", True
                    covered = True
        return ("allow" if covered else "deny"), covered
