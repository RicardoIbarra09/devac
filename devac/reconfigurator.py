# xolo/abac/reconfigurator.py
"""
APEX — PolicyReconfigurator

El reconfigurador escucha eventos de dominio y aplica reglas de transformación
pre-definidas a las políticas que declaran ese tipo de evento en `reconfigure_on`.

Diseño clave:
  - Una política NO se reescribe sola; el reconfigurador aplica una función de
    transformación registrada (rule_fn) que devuelve la política modificada.
  - El hot-swap es atómico: usa evaluator.update_policy() internamente.
  - Las reglas son funciones Python puras: reciben (Policy, DomainEvent) y
    devuelven la Policy modificada (o None si no hay cambio).

Jerarquía de niveles de reconfiguración (ReconfigurationLevel):
  Cada regla se registra con un nivel explícito. Se espera que la frecuencia
  de ejecución DISMINUYA a medida que el nivel aumenta: Nivel 1/2 reaccionan
  directo a eventos de dominio externos; Nivel 3 solo ocurre cuando una
  reconfiguración dispara otra (cascada, vía republicación de
  `politica_actualizada`); Nivel 4 (fuera de esta clase, ver ModularityMonitor)
  solo ocurre cuando el estrés acumulado de muchos cambios pequeños cruza un
  umbral. El reconfigurador mantiene un registro (`self.log`) de cada
  reconfiguración aplicada junto con su nivel, para que esta jerarquía sea
  medible en tiempo de ejecución y no solo una clasificación narrativa.

Uso:
    reconfigurator = PolicyReconfigurator(evaluator=ce, event_bus=bus)
    reconfigurator.register_rule("turno_terminado", on_turno_terminado,
                                  level=ReconfigurationLevel.PARAMETRICO)

    # Conectar al EventBus (necesario para que las cascadas de Nivel 3 fluyan)
    bus.subscribe("turno_terminado", reconfigurator.handle)
    await bus.publish(TurnoTerminado(space="urgencias"))
"""

import logging
from copy import deepcopy
from enum import IntEnum
from typing import Callable, Dict, List, Optional, Tuple

from devac.events import DomainEvent, EventBus, PoliticaActualizada
from devac.models import Policy
from devac.evaluator import CommunityPolicyEvaluator

log = logging.getLogger(__name__)

# Tipo de una función de transformación de política
RuleFn = Callable[[Policy, DomainEvent], Optional[Policy]]


class ReconfigurationLevel(IntEnum):
    """
    Nivel 1 — PARAMETRICO: cambia un valor dentro de un evento existente
              (p. ej. effect, o un atributo como accion/rango_horario).
    Nivel 2 — ESTRUCTURAL: agrega o quita eventos completos de una política,
              cambiando su superficie de cobertura, no solo un valor.
    Nivel 3 — CASCADA: la reconfiguración de una política dispara, vía el
              EventBus, la reconfiguración de otra(s) política(s) distinta(s).
    (Nivel 4 — TOPOLOGICO: reconstrucción completa del grafo/comunidades.
              No vive en esta clase — lo implementa ModularityMonitor,
              disparado por acumulación de estrés, no por una regla puntual.)
    """
    PARAMETRICO = 1
    ESTRUCTURAL = 2
    CASCADA = 3


class PolicyReconfigurator:
    """
    Aplica reglas de transformación a políticas cuando ocurre un DomainEvent.

    Flujo por evento:
      1. Recibe el evento (vía handle o directamente desde el EventBus).
      2. Itera sobre todas las políticas activas.
      3. Si la política declara event.event_type en su campo `reconfigure_on`,
         aplica todas las RuleFn registradas para ese tipo.
      4. Si alguna regla devuelve una política modificada, la hot-swapea
         llamando a evaluator.update_policy() y registra el cambio en self.log.
      5. Si se proporcionó un event_bus, republica `PoliticaActualizada` para
         esa política — esto es lo que habilita cascadas de Nivel 3 en
         cualquier otra política suscrita a ese evento. Se limita por
         MAX_CASCADE_DEPTH para evitar ciclos/cascadas infinitas.

    Thread-safety: update_policy() usa Atomic Swap internamente, por lo que
    el hot-swap es seguro ante lecturas concurrentes del evaluador.
    """

    MAX_CASCADE_DEPTH = 3

    def __init__(self, evaluator: CommunityPolicyEvaluator, event_bus: Optional[EventBus] = None):
        self.evaluator = evaluator
        self.event_bus = event_bus
        self._rules: Dict[str, List[Tuple[RuleFn, ReconfigurationLevel]]] = {}
        # Registro de reconfiguraciones realmente aplicadas (no solo intentadas),
        # usado para medir la jerarquía de frecuencias por nivel.
        self.log: List[dict] = []

    def register_rule(self, event_type: str, rule_fn: RuleFn, level: ReconfigurationLevel) -> None:
        """
        Registra una función de transformación para un tipo de evento, con su
        nivel de reconfiguración explícito. Se pueden registrar varias reglas
        para el mismo tipo; se aplican en orden.

        Args:
            event_type: nombre del evento (ej. "turno_terminado")
            rule_fn:    función (Policy, DomainEvent) -> Optional[Policy]
            level:      ReconfigurationLevel bajo el que se clasifica esta regla
        """
        self._rules.setdefault(event_type, []).append((rule_fn, level))
        log.debug("Regla registrada para '%s' (nivel %s): %s", event_type, level.name, rule_fn.__name__)

    def unregister_rules(self, event_type: str) -> None:
        """Elimina todas las reglas registradas para un tipo de evento."""
        self._rules.pop(event_type, None)

    async def handle(self, event: DomainEvent) -> None:
        """
        Handler compatible con EventBus (coroutine).
        Procesa el evento y aplica transformaciones a las políticas afectadas.
        """
        rules = self._rules.get(event.event_type, [])
        if not rules:
            return

        cascade_depth = int(event.payload.get("cascade_depth", 0))

        affected: List[str] = [
            pid
            for pid, policy in self.evaluator.policies.items()
            if event.event_type in policy.reconfigure_on
        ]

        changed_pids: List[str] = []

        for pid in affected:
            policy = self.evaluator.policies.get(pid)
            if policy is None:
                continue

            # Copia profunda para no mutar el estado en vivo durante la transformación
            working_copy = deepcopy(policy)
            changed = False
            applied_level: Optional[ReconfigurationLevel] = None

            try:
                for rule_fn, level in rules:
                    result = rule_fn(working_copy, event)
                    if result is not None:
                        working_copy = result
                        changed = True
                        applied_level = level
            except Exception:
                # Aislar el fallo a esta política: una regla que falla (p. ej.
                # sobre una política que ya quedó sin eventos por la auditoría
                # de redundancia) no debe abortar el resto del lote -- antes
                # una excepción aquí terminaba silenciosamente el procesamiento
                # de TODAS las políticas restantes de esta llamada a handle().
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
            self.evaluator.update_policy(working_copy)

            self.log.append({
                "policy_id": pid,
                "event_type": event.event_type,
                "level": applied_level,
                "cascade_depth": cascade_depth,
            })
            changed_pids.append(pid)

        # Nivel 3: republicar como UN SOLO evento de dominio agregado (no uno
        # por política) para que las políticas suscritas a "politica_actualizada"
        # reaccionen una vez por esta llamada a handle(), no una vez por cada
        # política afectada -- con disparadores compartidos por cientos de
        # políticas (p. ej. sesion_terminada), publicar por política individual
        # multiplica el trabajo de la cascada combinatoriamente. Se limita
        # además la profundidad para evitar cascadas/ciclos infinitos.
        if changed_pids and self.event_bus is not None and cascade_depth < self.MAX_CASCADE_DEPTH:
            await self.event_bus.publish(PoliticaActualizada(
                policy_id=changed_pids[0],
                source="reconfigurator",
                payload={"cascade_depth": cascade_depth + 1, "policy_ids": changed_pids},
            ))

    def handle_sync(self, event: DomainEvent) -> None:
        """
        Versión síncrona para pruebas o contextos sin event loop.
        """
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.handle(event))
            else:
                loop.run_until_complete(self.handle(event))
        except RuntimeError:
            asyncio.run(self.handle(event))

    # ------------------------------------------------------------------
    # Utilidades de introspección
    # ------------------------------------------------------------------

    def registered_event_types(self) -> List[str]:
        """Devuelve los tipos de evento que tienen al menos una regla registrada."""
        return list(self._rules.keys())

    def policies_affected_by(self, event_type: str) -> List[str]:
        """
        Devuelve los policy_id que serían reconfigurados si llegara un evento
        del tipo indicado.
        """
        return [
            pid
            for pid, policy in self.evaluator.policies.items()
            if event_type in policy.reconfigure_on
        ]

    def level_counts(self) -> Dict[str, int]:
        """
        Cuenta cuántas reconfiguraciones REALMENTE aplicadas (no solo
        intentadas) ocurrieron por nivel — la evidencia medible de la
        jerarquía de frecuencias esperada (Nivel 1/2 >> Nivel 3).
        """
        counts = {lvl.name: 0 for lvl in ReconfigurationLevel}
        for entry in self.log:
            counts[entry["level"].name] += 1
        return counts
