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
from devac.modularity import IncrementalModularity
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
        verify_modularity: bool = False,
    ):
        self.evaluator            = evaluator
        self.graph_builder        = graph_builder
        self.community_detector   = community_detector
        self.threshold            = threshold
        self.similarity_threshold = similarity_threshold
        self.audit_policies       = audit_policies
        self.conflict_priority    = conflict_priority
        self.last_audit_report    = None
        self.verify_modularity    = verify_modularity  # tambien calcula Q completa con networkx, para comparar

        self._stress: float          = 0.0  # suma de |dQ| desde la ultima reconstruccion
        self._stress_proxy: float    = 0.0  # version barata, sin recalcular Q
        self._q_current: float       = 0.0
        self._swap_count: int        = 0
        self._rebuilds: int          = 0
        self._rebuild_lock           = threading.Lock()
        self._rebuilding: bool       = False
        # grafo y Q cambian juntos: lo toman los cambios en caliente y la reconstruccion completa.
        # no protege al evaluador; quien mute politicas mientras corre una reconstruccion en
        # segundo plano tiene que serializar sus escrituras con ella (la API lo hace con write_lock)
        self.lock                    = threading.RLock()
        self._q = IncrementalModularity()

        # se ajusta cada vez que dispara el umbral real
        self._threshold_proxy: Optional[float] = None

        self.history: List[Dict] = []  # un registro por swap para comparar real vs proxy

        if graph_builder.graph is not None:
            self._reset_modularity()

        # se entera de cada cambio de politica y decide la comunidad de las nuevas
        evaluator.register_policy_change_hook(self.on_policy_change)
        evaluator.community_assigner = self.best_community

    @property
    def q(self) -> float:
        return self._q.q

    # el grafo sigue a las politicas: salen los nodos de la version anterior y entran los de la nueva.
    # Q se actualiza solo con los vecinos de esos nodos
    def on_policy_change(self, old: Optional[Policy], new: Optional[Policy]) -> float:
        with self.lock:
            policy = new if new is not None else old
            # el proxy mira el grafo antes del cambio, como lo hacia antes
            t0 = time.perf_counter()
            delta_proxy = self._proxy_delta(policy)
            t_proxy = time.perf_counter() - t0

            builder = self.graph_builder
            leaving = [e.event_id for e in old.events] if old is not None else []
            if new is not None:
                # un event_id repetido en otra politica reemplaza al nodo anterior
                leaving += [e.event_id for e in new.events if e.event_id in builder]

            t0 = time.perf_counter()
            for event_id in leaving:
                self._q.remove_node(builder.graph, event_id)
            t_incremental = time.perf_counter() - t0

            t0 = time.perf_counter()
            builder.remove_events(leaving)
            added = builder.add_events(new) if new is not None else []
            t_graph = time.perf_counter() - t0

            comm = self.evaluator.policy_community.get(new.policy_id) if new is not None else None
            t0 = time.perf_counter()
            if comm is not None:
                for event_id in added:
                    self._q.add_node(builder.graph, event_id, comm)
            t_incremental += time.perf_counter() - t0

            if old is None:
                op = "add"
            elif new is None:
                op = "remove"
            else:
                op = "move" if old is new else "update"
            return self.record_swap(policy, op=op, delta_proxy=delta_proxy, t_proxy=t_proxy,
                                    t_incremental=t_incremental, t_graph=t_graph)

    # comunidad donde la politica nueva sube mas Q, con la ganancia de Louvain para mover un bloque B
    # (sus eventos) desde una comunidad propia a c:  dQ = K_Bc/m - D_c*K_B/(2 m^2), donde K_Bc es el
    # peso de B hacia c, K_B el grado de B y m, D_c ya cuentan las aristas de B.
    # None si ninguna ganancia es positiva: la politica abre su propia comunidad
    def best_community(self, policy: Policy) -> Optional[object]:
        with self.lock:
            builder = self.graph_builder
            if not policy.events:
                return None
            if not builder.weights:
                # nunca hubo build: sin pesos no hay aristas con que medir la ganancia
                return self.evaluator.most_similar_community(policy)

            own_ids = {e.event_id for e in policy.events}
            to_comm: Dict[object, float] = {}
            k_block = 0.0
            m = self._q.m
            for event in policy.events:
                for other_id, w in builder.similar_events(event).items():
                    comm = self._q.community.get(other_id)
                    if comm is None or other_id in own_ids:
                        continue
                    to_comm[comm] = to_comm.get(comm, 0.0) + w
                    k_block += w
                    m += w
            # aristas entre eventos de la misma politica: cuentan en K_B y en m, no en K_Bc
            events = policy.events
            for i in range(len(events)):
                for j in range(i + 1, len(events)):
                    sim = builder.event_similarity(events[i], events[j], builder.weights)
                    if sim >= builder.similarity_threshold:
                        k_block += 2 * sim
                        m += sim
            if m <= 0:
                return None

            best, best_gain = None, 0.0
            for comm, k_in in to_comm.items():
                d_c = self._q.degree.get(comm, 0.0) + k_in
                gain = k_in / m - d_c * k_block / (2 * m * m)
                if gain > best_gain:
                    best, best_gain = comm, gain
            return best

    # delta_proxy=None: se calcula aqui sobre el grafo actual
    def record_swap(self, policy: Optional[Policy] = None, *, op: Optional[str] = None,
                    delta_proxy: Optional[float] = None, t_proxy: float = 0.0,
                    t_incremental: float = 0.0, t_graph: float = 0.0) -> float:
        if self.graph_builder.graph is None:
            return 0.0

        q_new = self._q.q
        delta = abs(q_new - self._q_current)
        self._q_current = q_new
        self._stress   += delta
        self._swap_count += 1

        if delta_proxy is None:
            t0 = time.perf_counter()
            delta_proxy = self._proxy_delta(policy)  # O(k), k = eventos de la politica
            t_proxy = time.perf_counter() - t0
        self._stress_proxy += delta_proxy

        q_networkx = t_networkx = None
        if self.verify_modularity:
            t0 = time.perf_counter()
            q_networkx = self._compute_modularity(self.graph_builder.graph)  # O(N+E)
            t_networkx = time.perf_counter() - t0

        threshold_proxy_active = self._threshold_proxy

        self.history.append({
            "swap": self._swap_count,
            "op": op,
            "policy_id": policy.policy_id if policy is not None else None,
            "q_real": q_new,  # ahora es la Q incremental, exacta para el grafo vigente
            "q_incremental": q_new,
            "q_networkx": q_networkx,
            "delta_q": delta,
            "stress_q": self._stress,
            "t_real_s": t_networkx,  # lo que costaba antes cada cambio: Q completa
            "t_networkx_s": t_networkx,
            "t_incremental_s": t_incremental,
            "t_graph_s": t_graph,  # actualizar nodos y aristas, lo necesitan los dos metodos
            "delta_proxy": delta_proxy,
            "stress_proxy": self._stress_proxy,
            "t_proxy_s": t_proxy,
            "threshold_proxy": threshold_proxy_active,
            "rebuilds": self._rebuilds,
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
        with self.lock:
            self._rebuild_locked()

    def _rebuild_locked(self) -> None:
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

        # los redundantes quedan fuera de los indices, no de sus politicas
        excluded = {}
        if self.audit_policies:
            policies, policies_by_community, self.last_audit_report = audit_and_mitigate(
                policies, policies_by_community, conflict_priority=self.conflict_priority
            )
            excluded = self.last_audit_report.exclusions()
            log.info("[ModularityMonitor] Auditoría de políticas: %s", self.last_audit_report.summary())

        self.evaluator.run(policies, policies_by_community, excluded)  # swap en el evaluador

        self._reset_modularity()  # pesos y particion nuevos: los dos desgastes vuelven a cero
        self.reset_stress()
        self._rebuilds += 1

        log.info(
            "[ModularityMonitor] Reconstrucción completada — Q=%.4f  rebuilds=%d",
            self._q_current, self._rebuilds,
        )

    # nodo -> comunidad segun el evaluador; los nodos de politicas sin comunidad no cuentan
    def _partition(self, graph: nx.Graph) -> Dict[str, object]:
        node_community = {}
        for comm, policy_ids in self.evaluator.policies_by_community.items():
            for pid in policy_ids:
                policy = self.evaluator.policies.get(pid)
                if policy:
                    for event in policy.events:
                        if event.event_id in graph:
                            node_community[event.event_id] = comm
        return node_community

    # O(N+E): solo al arrancar y despues de cada reconstruccion
    def _reset_modularity(self) -> None:
        graph = self.graph_builder.graph
        self._q = IncrementalModularity.from_partition(graph, self._partition(graph))
        self._q_current = self._q.q

    # Q completa con networkx; ya no se usa para el estres, queda para verificar la incremental
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
