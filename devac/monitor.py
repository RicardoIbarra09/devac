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
from devac.audit import audit_and_mitigate, order_by_priority
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
        self.last_rebuild_stats: Dict[str, object] = {}  # tiempos por fase de la ultima reconstruccion
        self.verify_modularity    = verify_modularity  # tambien calcula Q completa con networkx, para comparar

        self._stress: float          = 0.0  # suma de |dQ| desde la ultima reconstruccion
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

        self.history: List[Dict] = []  # un registro por cambio

        if graph_builder.graph is not None:
            self._reset_modularity()

        # se entera de cada cambio de politica y decide la comunidad de las nuevas
        evaluator.register_policy_change_hook(self.on_policy_change)
        evaluator.community_assigner = self.best_community

    @property
    def q(self) -> float:
        return self._q.q

    # el grafo sigue a lo que usa el evaluador: salen los nodos de la version anterior, entran los de
    # la nueva (menos los excluidos por la auditoria) y los liberados de otras politicas.
    # Q se actualiza solo con los vecinos de esos nodos
    def on_policy_change(self, old: Optional[Policy], new: Optional[Policy],
                         released: Optional[Dict[str, str]] = None) -> float:
        with self.lock:
            policy = new if new is not None else old
            builder = self.graph_builder
            # una politica inactiva conserva sus eventos, pero no tiene nodos
            old_events = old.events if old is not None and old.active else []
            new_events = new.events if new is not None and new.active else []
            leaving = [e.event_id for e in old_events]
            # un event_id repetido en otra politica reemplaza al nodo anterior
            leaving += [e.event_id for e in new_events if e.event_id in builder]

            t0 = time.perf_counter()
            for event_id in leaving:
                self._q.remove_node(builder.graph, event_id)
            t_incremental = time.perf_counter() - t0

            excluded = self.evaluator.excluded
            entering = []  # (politica, eventos)
            if new_events:
                entering.append((new, [e for e in new_events if e.event_id not in excluded]))
            by_owner: Dict[str, List[str]] = {}
            for event_id, owner in (released or {}).items():
                by_owner.setdefault(owner, []).append(event_id)
            for owner, event_ids in by_owner.items():
                policy_owner = self.evaluator.policies.get(owner)
                if policy_owner is not None:
                    wanted = set(event_ids)
                    entering.append((policy_owner, [e for e in policy_owner.events
                                                    if e.event_id in wanted and e.event_id not in builder]))

            t0 = time.perf_counter()
            builder.remove_events(leaving)
            added = [(p, builder.add_events(p, events)) for p, events in entering]
            t_graph = time.perf_counter() - t0

            t0 = time.perf_counter()
            for p, event_ids in added:
                comm = self.evaluator.policy_community.get(p.policy_id)
                if comm is not None:
                    for event_id in event_ids:
                        self._q.add_node(builder.graph, event_id, comm)
            t_incremental += time.perf_counter() - t0

            if old is None:
                op = "add"
            elif new is None:
                op = "remove"
            elif old.active != new.active:
                op = "activate" if new.active else "deactivate"
            else:
                op = "move" if old is new else "update"
            return self.record_swap(policy, op=op, t_incremental=t_incremental, t_graph=t_graph)

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

    def record_swap(self, policy: Optional[Policy] = None, *, op: Optional[str] = None,
                    t_incremental: float = 0.0, t_graph: float = 0.0) -> float:
        if self.graph_builder.graph is None:
            return 0.0

        q_new = self._q.q
        delta = abs(q_new - self._q_current)
        self._q_current = q_new
        self._stress   += delta
        self._swap_count += 1

        q_networkx = t_networkx = None
        if self.verify_modularity:
            t0 = time.perf_counter()
            q_networkx = self._compute_modularity(self.graph_builder.graph)  # O(N+E)
            t_networkx = time.perf_counter() - t0

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
            "rebuilds": self._rebuilds,
        })

        log.debug(
            "Swap #%d — Q=%.4f  ΔQ=%.4f  estrés=%.4f  umbral=%.4f",
            self._swap_count, q_new, delta, self._stress, self.threshold,
        )

        # suficiente desgaste: reconstruccion nivel 4
        if self._stress >= self.threshold and not self._rebuilding:
            log.info(
                "Estrés %.4f ≥ umbral %.4f — iniciando reconstrucción de grafo en segundo plano",
                self._stress, self.threshold,
            )
            self._trigger_rebuild()

        return delta

    async def on_policy_event(self, event: DomainEvent) -> None:
        policy_id = getattr(event, "policy_id", None)
        policy = self.evaluator.policies.get(policy_id) if policy_id else None
        self.record_swap(policy)

    def get_stats(self) -> dict:
        return {
            "q_current":       round(self._q_current, 6),
            "stress":          round(self._stress, 6),
            "threshold":       self.threshold,
            "swap_count":      self._swap_count,
            "rebuilds":        self._rebuilds,
            "rebuilding":      self._rebuilding,
        }

    def reset_stress(self) -> None:
        self._stress = 0.0

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
        everything = list(self.evaluator.policies.values())
        # solo las activas entran al grafo, a louvain y a la auditoria; las inactivas siguen en el
        # evaluador, sin comunidad, con sus eventos
        policies = [p for p in everything if p.active]
        stats: Dict[str, object] = {}
        clock = time.perf_counter

        # la misma semilla en las dos pasadas de louvain
        seed = self.community_detector.next_seed()
        stats["louvain_seed"] = seed

        t0 = clock()
        graph = self.graph_builder.build_event_graph(policies, similarity_threshold=self.similarity_threshold)
        stats["t_build_s"] = clock() - t0

        t0 = clock()
        policies_by_community = self._louvain(graph, policies, seed)
        stats["t_louvain_s"] = clock() - t0

        # los redundantes quedan fuera de los indices, no de sus politicas
        excluded = {}
        stats["t_audit_s"] = 0.0
        if self.audit_policies:
            t0 = clock()
            policies, policies_by_community, self.last_audit_report = audit_and_mitigate(
                policies, policies_by_community, conflict_priority=self.conflict_priority
            )
            excluded = self.last_audit_report.exclusions()
            stats["t_audit_s"] = clock() - t0
            log.info("[ModularityMonitor] Auditoría de políticas: %s", self.last_audit_report.summary())
        stats["excluded"] = len(excluded)

        # la auditoria necesita comunidades, pero la particion final se calcula sobre el grafo que de
        # verdad se usa: sin los excluidos y con pesos de esos eventos. una politica con todos sus
        # eventos excluidos se queda sin comunidad hasta que se libere alguno
        stats["t_build_used_s"] = stats["t_louvain_used_s"] = 0.0
        if excluded:
            used = [p.model_copy(update={"events": [e for e in p.events if e.event_id not in excluded]})
                    for p in policies]
            t0 = clock()
            graph = self.graph_builder.build_event_graph(used, similarity_threshold=self.similarity_threshold)
            stats["t_build_used_s"] = clock() - t0
            t0 = clock()
            policies_by_community = self._louvain(graph, used, seed)
            if self.last_audit_report is not None:
                policies_by_community, _ = order_by_priority(
                    policies_by_community, self.last_audit_report.conflicts, self.conflict_priority
                )
            stats["t_louvain_used_s"] = clock() - t0

        t0 = clock()
        audited = {p.policy_id: p for p in policies}
        self.evaluator.run([audited.get(p.policy_id, p) for p in everything], policies_by_community,
                           excluded)  # swap en el evaluador
        stats["t_index_s"] = clock() - t0

        t0 = clock()
        self._reset_modularity()  # pesos y particion nuevos: los dos desgastes vuelven a cero
        stats["t_q_s"] = clock() - t0
        stats["communities"] = len(policies_by_community)
        self.last_rebuild_stats = stats
        self.reset_stress()
        self._rebuilds += 1

        log.info(
            "[ModularityMonitor] Reconstrucción completada — Q=%.4f  rebuilds=%d",
            self._q_current, self._rebuilds,
        )

    def _louvain(self, graph: nx.Graph, policies: List[Policy], seed: Optional[int]) -> Dict[object, List[str]]:
        _, event_to_community = self.community_detector.detect_communities(graph, seed=seed)
        event_to_policy = {e.event_id: p.policy_id for p in policies for e in p.events}
        return self.community_detector.map_policies_to_communities(event_to_policy, event_to_community)

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
