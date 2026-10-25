import asyncio
import logging
import threading
import time
from typing import Dict, List, Optional

import networkx as nx
from networkx.algorithms.community import modularity

from devac.events import DomainEvent
from devac.evaluator import CommunityPolicyEvaluator
from devac.graph import GraphBuilder
from devac.communities import CommunityDetector
from devac.audit import audit_and_mitigate
from devac.models import Policy

log = logging.getLogger(__name__)


class ModularityMonitor:
    def __init__(
        self,
        evaluator: CommunityPolicyEvaluator,
        graph_builder: GraphBuilder,
        community_detector: CommunityDetector,
        threshold: float = 0.5,
        similarity_threshold: float = 0.3,
        audit_policies: bool = True,
        conflict_priority: str = "deny",
    ):
        self.evaluator            = evaluator
        self.graph_builder        = graph_builder
        self.community_detector   = community_detector
        self.threshold            = threshold
        self.similarity_threshold = similarity_threshold
        self.audit_policies       = audit_policies
        self.conflict_priority    = conflict_priority
        self.last_audit_report    = None

        self._stress: float          = 0.0  # suma de |dQ| desde la ultima reconstruccion
        self._stress_proxy: float    = 0.0  # version barata, sin recalcular Q
        self._q_current: float       = 0.0
        self._swap_count: int        = 0
        self._rebuilds: int          = 0
        self._rebuild_lock           = threading.Lock()
        self._rebuilding: bool       = False

        # se ajusta cada vez que dispara el umbral real
        self._threshold_proxy: Optional[float] = None

        self.history: List[Dict] = []  # un registro por swap para comparar real vs proxy

        if graph_builder.graph is not None:
            self._q_current = self._compute_modularity(graph_builder.graph)

        # se entera de cada cambio de politica
        evaluator.register_policy_change_hook(self.record_swap)

    def record_swap(self, policy: Optional[Policy] = None) -> float:
        if self.graph_builder.graph is None:
            return 0.0

        t0 = time.perf_counter()
        q_new = self._compute_modularity(self.graph_builder.graph)  # O(N+E), es lo caro
        t_real = time.perf_counter() - t0
        delta = abs(q_new - self._q_current)
        self._q_current = q_new
        self._stress   += delta
        self._swap_count += 1

        t0 = time.perf_counter()
        delta_proxy = self._proxy_delta(policy)  # O(k), k = eventos de la politica
        t_proxy = time.perf_counter() - t0
        self._stress_proxy += delta_proxy

        threshold_proxy_active = self._threshold_proxy

        self.history.append({
            "swap": self._swap_count,
            "q_real": q_new,
            "delta_q": delta,
            "stress_q": self._stress,
            "t_real_s": t_real,
            "delta_proxy": delta_proxy,
            "stress_proxy": self._stress_proxy,
            "t_proxy_s": t_proxy,
            "threshold_proxy": threshold_proxy_active,
        })

        log.debug(
            "Swap #%d — Q=%.4f  ΔQ=%.4f  estrés=%.4f  |  proxy Δ=%.6f  estrés_proxy=%.4f  umbral=%.4f  umbral_proxy=%s",
            self._swap_count, q_new, delta, self._stress, delta_proxy, self._stress_proxy, self.threshold,
            threshold_proxy_active,
        )

        # suficiente desgaste: reconstruccion nivel 4
        if self._stress >= self.threshold and not self._rebuilding:
            log.info(
                "Estrés %.4f ≥ umbral %.4f — iniciando reconstrucción de grafo en segundo plano",
                self._stress, self.threshold,
            )
            self._threshold_proxy = self._stress_proxy  # el proxy aprende el umbral de este ciclo
            self._trigger_rebuild()

        return delta

    # suma k_i/2m de los eventos tocados
    def _proxy_delta(self, policy: Optional[Policy]) -> float:
        graph = self.graph_builder.graph
        if graph is None or policy is None:
            return 0.0

        m = graph.number_of_edges()
        if m == 0:
            return 0.0

        n = graph.number_of_nodes()
        avg_degree = (2 * m / n) if n else 0.0

        total = 0.0
        for event in policy.events:
            # evento nuevo: se usa el grado promedio
            k = graph.degree(event.event_id) if event.event_id in graph else avg_degree
            total += k / (2 * m)
        return total

    async def on_policy_event(self, event: DomainEvent) -> None:
        policy_id = getattr(event, "policy_id", None)
        policy = self.evaluator.policies.get(policy_id) if policy_id else None
        self.record_swap(policy)

    def get_stats(self) -> dict:
        return {
            "q_current":       round(self._q_current, 6),
            "stress":          round(self._stress, 6),
            "stress_proxy":    round(self._stress_proxy, 6),
            "threshold":       self.threshold,
            "threshold_proxy": round(self._threshold_proxy, 6) if self._threshold_proxy is not None else None,
            "swap_count":      self._swap_count,
            "rebuilds":        self._rebuilds,
            "rebuilding":      self._rebuilding,
        }

    def reset_stress(self) -> None:
        self._stress = 0.0
        self._stress_proxy = 0.0

    # reconstruye en un hilo aparte para no bloquear
    def _trigger_rebuild(self) -> None:
        with self._rebuild_lock:
            if self._rebuilding:
                return
            self._rebuilding = True

        t = threading.Thread(target=self._rebuild_worker, daemon=True, name="apex-graph-rebuild")
        t.start()

    # igual pero en el mismo hilo y los errores si se propagan
    def rebuild_now(self) -> bool:
        with self._rebuild_lock:
            if self._rebuilding:
                return False
            self._rebuilding = True
        try:
            self._rebuild()
            return True
        finally:
            with self._rebuild_lock:
                self._rebuilding = False

    def _rebuild_worker(self) -> None:
        try:
            self._rebuild()
        except Exception as exc:
            log.error("[ModularityMonitor] Error en reconstrucción: %s", exc, exc_info=True)
        finally:
            with self._rebuild_lock:
                self._rebuilding = False

    # grafo -> louvain -> auditoria -> indices -> Q
    def _rebuild(self) -> None:
        log.info("[ModularityMonitor] Reconstruyendo grafo…")
        policies = list(self.evaluator.policies.values())

        graph = self.graph_builder.build_event_graph(
            policies, similarity_threshold=self.similarity_threshold
        )

        event_communities, event_to_community = self.community_detector.detect_communities(graph)

        event_to_policy = {}
        for policy in policies:
            for event in policy.events:
                event_to_policy[event.event_id] = policy.policy_id

        policies_by_community = self.community_detector.map_policies_to_communities(
            event_to_policy, event_to_community
        )

        # se limpia antes de armar los indices
        if self.audit_policies:
            policies, policies_by_community, self.last_audit_report = audit_and_mitigate(
                policies, policies_by_community, conflict_priority=self.conflict_priority
            )
            log.info("[ModularityMonitor] Auditoría de políticas: %s", self.last_audit_report.summary())

        self.evaluator.run(policies, policies_by_community)  # swap en el evaluador

        self._q_current = self._compute_modularity(graph)
        self.reset_stress()
        self._rebuilds += 1

        log.info(
            "[ModularityMonitor] Reconstrucción completada — Q=%.4f  rebuilds=%d",
            self._q_current, self._rebuilds,
        )

    def _compute_modularity(self, graph: nx.Graph) -> float:
        if graph.number_of_edges() == 0:
            return 0.0

        communities = []
        covered: set = set()
        for policy_ids in self.evaluator.policies_by_community.values():
            node_set = set()
            for pid in policy_ids:
                policy = self.evaluator.policies.get(pid)
                if policy:
                    for event in policy.events:
                        if event.event_id in graph.nodes:
                            node_set.add(event.event_id)
            if node_set:
                communities.append(node_set)
                covered.update(node_set)

        if not communities:
            return 0.0

        # politicas vacias dejan nodos sin comunidad y modularity() truena
        subgraph = graph.subgraph(covered) if len(covered) < graph.number_of_nodes() else graph

        try:
            return modularity(subgraph, communities, weight="weight")
        except Exception:
            return 0.0
