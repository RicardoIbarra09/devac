import asyncio
import importlib
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from devac.baseline.evaluator import TraditionalPolicyEvaluator
from devac.communities import CommunityDetector
from devac.dpl import DPLLoader
from devac.evaluator import CommunityPolicyEvaluator
from devac.events import DomainEvent, EventBus, PoliticaActualizada, PoliticaAgregada, PoliticaEliminada
from devac.graph import GraphBuilder
from devac.loader import PolicyLoader
from devac.models import AccessRequest, Policy
from devac.monitor import ModularityMonitor
from devac.reconfigurator import PolicyReconfigurator

from devac_api.config import Settings
from devac_api.mapping import canonicalize_policy
from devac_api.schemas import Engine

log = logging.getLogger(__name__)


class EngineError(Exception):
    pass


class PolicyNotFound(EngineError):
    pass


class PolicyConflict(EngineError):
    pass


class RebuildInProgress(EngineError):
    pass


# igual que GraphBuilder pero el export es opcional
class _ServiceGraphBuilder(GraphBuilder):
    def __init__(self, export: bool):
        super().__init__()
        self._export = export

    def export_graph(self, output_path: str, format: str = "gexf") -> None:
        if self._export:
            super().export_graph(output_path, format)


# arma el nucleo completo; es lo unico que la API toca
class DevacEngine:
    def __init__(self, settings: Settings, policies: Iterable[Policy] = ()):
        self.settings = settings

        # dict nuevo, los defaults del constructor son mutables
        self.evaluator = CommunityPolicyEvaluator([], {})
        self.graph_builder = _ServiceGraphBuilder(settings.export_graph)
        self.detector = CommunityDetector()
        self.bus = EventBus()
        self.reconfigurator = PolicyReconfigurator(self.evaluator, self.bus)
        # el reconfigurador escucha todos los eventos
        self.bus.subscribe("*", self.reconfigurator.handle)
        self.monitor = ModularityMonitor(
            self.evaluator,
            self.graph_builder,
            self.detector,
            threshold=settings.stress_threshold,
            similarity_threshold=settings.similarity_threshold,
            audit_policies=settings.audit,
            conflict_priority=settings.conflict_priority,
        )

        self._mutations = 0
        self.evaluator.register_policy_change_hook(self._on_policy_change)
        self._baseline: Optional[TraditionalPolicyEvaluator] = None
        self._baseline_key: Optional[tuple] = None

        self.write_lock = asyncio.Lock()  # un solo escritor; las lecturas no usan lock
        self._background: set = set()  # eventos encolados con wait=false

        initial = [canonicalize_policy(p) for p in policies]
        self._ensure_unique(initial, existing={})
        self.evaluator.run(initial, {})
        if initial:
            self.monitor.rebuild_now()  # build inicial = la misma reconstruccion de nivel 4

        if settings.rules_module:
            self._load_rules(settings.rules_module)

    @classmethod
    def from_settings(cls, settings: Settings) -> "DevacEngine":
        policies: List[Policy] = []
        if settings.policy_file:
            path = Path(settings.policy_file)
            loader = DPLLoader if path.suffix.lower() == ".dpl" else PolicyLoader
            policies = loader.from_file(str(path))
            log.info("Cargadas %d políticas desde %s", len(policies), path)
        return cls(settings, policies)

    def evaluate(self, request: AccessRequest, engine: Engine = Engine.devac) -> dict:
        if engine is Engine.baseline:
            return self._baseline_evaluator().evaluate(request)
        return self.evaluator.evaluate(request)

    def _baseline_evaluator(self) -> TraditionalPolicyEvaluator:
        # se rehace el baseline solo si cambiaron las politicas
        key = (self._mutations, self.monitor.get_stats()["rebuilds"], id(self.evaluator.policies))
        if key != self._baseline_key:
            self._baseline = TraditionalPolicyEvaluator(list(self.evaluator.policies.values()))
            self._baseline_key = key
        return self._baseline

    def _on_policy_change(self, _policy: Optional[Policy]) -> None:
        self._mutations += 1

    def community_map(self) -> Dict[str, str]:
        return {
            pid: str(comm)
            for comm, pids in self.evaluator.policies_by_community.items()
            for pid in pids
        }

    def get_policy(self, policy_id: str) -> Policy:
        policy = self.evaluator.policies.get(policy_id)
        if policy is None:
            raise PolicyNotFound(policy_id)
        return policy

    def stats(self) -> dict:
        policies = self.evaluator.policies
        report = self.monitor.last_audit_report
        return {
            "policies": len(policies),
            "active_policies": sum(1 for p in policies.values() if p.events),
            "communities": len(self.evaluator.policies_by_community),
            "events": sum(len(evs) for evs in self.evaluator.events_by_comm.values()),
            "reconfigurations_applied": len(self.reconfigurator.log),
            "monitor": self.monitor.get_stats(),
            "last_audit": report.summary() if report is not None else None,
        }

    async def add_policies(self, policies: List[Policy]) -> List[Policy]:
        policies = [canonicalize_policy(p) for p in policies]
        async with self.write_lock:
            self._ensure_unique(policies, existing=self.evaluator.policies)
            for policy in policies:
                self._insert(policy)
            for policy in policies:
                # los cambios hechos por la API tambien son eventos de dominio
                await self.bus.publish(PoliticaAgregada(policy_id=policy.policy_id, source="api"))
        return [self.evaluator.policies[p.policy_id] for p in policies]

    async def update_policy(self, policy: Policy) -> Policy:
        policy = canonicalize_policy(policy)
        async with self.write_lock:
            if policy.policy_id not in self.evaluator.policies:
                raise PolicyNotFound(policy.policy_id)
            others = {pid: p for pid, p in self.evaluator.policies.items() if pid != policy.policy_id}
            self._ensure_unique([policy], existing=others)

            # politica activa: hot-swap normal
            if policy.policy_id in self.community_map():
                self.evaluator.update_policy(policy)
            else:
                # estaba inactiva, se vuelve a insertar
                self.evaluator.policies.pop(policy.policy_id)
                self._insert(policy)
            await self.bus.publish(PoliticaActualizada(policy_id=policy.policy_id, source="api"))
        return self.evaluator.policies[policy.policy_id]

    async def delete_policy(self, policy_id: str) -> None:
        async with self.write_lock:
            if policy_id not in self.evaluator.policies:
                raise PolicyNotFound(policy_id)
            self.evaluator.remove_policy(policy_id)
            await self.bus.publish(PoliticaEliminada(policy_id=policy_id, source="api"))

    def _insert(self, policy: Policy) -> None:
        if self.evaluator.policies_by_community:
            self.evaluator.add_policy(policy, auto_assign_community=True)
        else:
            self.evaluator.add_policy(policy, auto_assign_community=False)
            # primera politica: abre la comunidad 0
            self.evaluator.assign_policy_to_community(policy.policy_id, 0)

    @staticmethod
    def _ensure_unique(policies: List[Policy], existing: Dict[str, Policy]) -> None:
        seen_policies = set(existing)
        # event_id es el id del nodo, no se puede repetir
        seen_events = {e.event_id for p in existing.values() for e in p.events}
        for policy in policies:
            if policy.policy_id in seen_policies:
                raise PolicyConflict(f"la política '{policy.policy_id}' ya existe")
            seen_policies.add(policy.policy_id)
            for event in policy.events:
                if event.event_id in seen_events:
                    raise PolicyConflict(f"el evento '{event.event_id}' ya existe en otra política")
                seen_events.add(event.event_id)

    async def publish_event(self, event: DomainEvent, wait: bool = True) -> Optional[List[dict]]:
        if not wait:
            # se procesa despues, la respuesta no espera
            task = asyncio.create_task(self._process_event(event))
            self._background.add(task)
            task.add_done_callback(self._background.discard)
            return None
        return await self._process_event(event)

    async def _process_event(self, event: DomainEvent) -> List[dict]:
        async with self.write_lock:
            start = len(self.reconfigurator.log)  # para devolver solo lo que hizo este evento
            await self.bus.publish(event)
            return self.reconfigurator.log[start:]

    async def rebuild(self) -> None:
        async with self.write_lock:
            if not self.evaluator.policies:
                return
            # en otro hilo para seguir atendiendo evaluaciones
            done = await asyncio.to_thread(self.monitor.rebuild_now)
            if not done:
                raise RebuildInProgress("ya hay una reconstrucción en curso")

    async def aclose(self) -> None:
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)

    def _load_rules(self, module_name: str) -> None:
        module = importlib.import_module(module_name)
        register = getattr(module, "register", None)
        if not callable(register):
            raise RuntimeError(f"{module_name} debe definir register(reconfigurator)")
        register(self.reconfigurator)  # el modulo registra sus reglas
        log.info("Reglas de reconfiguración cargadas desde %s", module_name)
