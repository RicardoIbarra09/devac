import logging
from copy import deepcopy
from enum import IntEnum
from typing import Callable, Dict, List, Optional, Tuple

from devac.events import DomainEvent, EventBus, PoliticaActualizada
from devac.models import Policy
from devac.evaluator import CommunityPolicyEvaluator

log = logging.getLogger(__name__)

# regla: devuelve la politica cambiada o None
RuleFn = Callable[[Policy, DomainEvent], Optional[Policy]]


class ReconfigurationLevel(IntEnum):
    PARAMETRICO = 1  # cambia un valor
    ESTRUCTURAL = 2  # agrega o quita eventos
    CASCADA = 3  # un cambio dispara otros cambios; el nivel 4 vive en el monitor


class PolicyReconfigurator:
    MAX_CASCADE_DEPTH = 3  # evita cascadas infinitas

    def __init__(self, evaluator: CommunityPolicyEvaluator, event_bus: Optional[EventBus] = None):
        self.evaluator = evaluator
        self.event_bus = event_bus
        self._rules: Dict[str, List[Tuple[RuleFn, ReconfigurationLevel]]] = {}
        self.log: List[dict] = []  # reconfiguraciones que si se aplicaron

    def register_rule(self, event_type: str, rule_fn: RuleFn, level: ReconfigurationLevel) -> None:
        self._rules.setdefault(event_type, []).append((rule_fn, level))
        log.debug("Regla registrada para '%s' (nivel %s): %s", event_type, level.name, rule_fn.__name__)

    def unregister_rules(self, event_type: str) -> None:
        self._rules.pop(event_type, None)

    async def handle(self, event: DomainEvent) -> None:
        rules = self._rules.get(event.event_type, [])
        if not rules:
            return

        cascade_depth = int(event.payload.get("cascade_depth", 0))

        affected: List[str] = [
            pid
            for pid, policy in self.evaluator.policies.items()
            # solo las politicas que declaran este evento
            if event.event_type in policy.reconfigure_on
        ]

        changed_pids: List[str] = []

        for pid in affected:
            policy = self.evaluator.policies.get(pid)
            if policy is None:
                continue

            working_copy = deepcopy(policy)  # las reglas nunca tocan la politica en vivo
            changed = False
            applied_level: Optional[ReconfigurationLevel] = None

            try:
                for rule_fn, level in rules:
                    result = rule_fn(working_copy, event)
                    if result is not None:
                        working_copy = result
                        changed = True
                        applied_level = level
            # si una regla falla se salta esa politica y sigue con las demas
            except Exception:
                log.exception(
                    "Regla falló al procesar la política '%s' para el evento '%s' — se omite, continúa el lote",
                    pid, event.event_type,
                )
                continue

            if not changed:
                continue

            log.info(
                "Hot-swap de política '%s' disparado por evento '%s' (nivel=%s, profundidad=%d, source=%s)",
                pid, event.event_type, applied_level.name, cascade_depth, event.source,
            )
            self.evaluator.update_policy(working_copy)  # hot-swap

            self.log.append({
                "policy_id": pid,
                "event_type": event.event_type,
                "level": applied_level,
                "cascade_depth": cascade_depth,
            })
            changed_pids.append(pid)

        # un solo evento con todas las cambiadas, para la cascada
        if changed_pids and self.event_bus is not None and cascade_depth < self.MAX_CASCADE_DEPTH:
            await self.event_bus.publish(PoliticaActualizada(
                policy_id=changed_pids[0],
                source="reconfigurator",
                payload={"cascade_depth": cascade_depth + 1, "policy_ids": changed_pids},
            ))

    def handle_sync(self, event: DomainEvent) -> None:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.handle(event))
            else:
                loop.run_until_complete(self.handle(event))
        except RuntimeError:
            asyncio.run(self.handle(event))

    def registered_event_types(self) -> List[str]:
        return list(self._rules.keys())

    def policies_affected_by(self, event_type: str) -> List[str]:
        return [
            pid
            for pid, policy in self.evaluator.policies.items()
            if event_type in policy.reconfigure_on
        ]

    def level_counts(self) -> Dict[str, int]:
        counts = {lvl.name: 0 for lvl in ReconfigurationLevel}
        for entry in self.log:
            counts[entry["level"].name] += 1
        return counts
