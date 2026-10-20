# xolo/abac/monitor.py
"""
APEX — ModularityMonitor

Calcula el delta de modularidad de la estructura comunitaria tras cada hot-swap
y acumula un índice de estrés.  Cuando el estrés supera un umbral, dispara
la reconstrucción completa del grafo en segundo plano.

Concepto:
  - Modularidad Q: métrica estándar de NetworkX que mide la calidad de la
    partición en comunidades.  Q ∈ [-1, 1]; valores altos = buena separación.
  - Delta Q por hot-swap: |Q_nueva - Q_anterior|.  Si muchos hot-swaps acumulan
    deltas grandes, la partición comunitaria original ya no es representativa.
  - Índice de estrés: suma acumulada de |ΔQ| desde la última reconstrucción.
  - Umbral: cuando estrés ≥ threshold, se reconstruye el grafo (y se resetea).

Estrés proxy (aproximación barata, corre en paralelo a la real):
  Recalcular Q exacta cuesta O(N+E) del grafo COMPLETO en cada hot-swap,
  aunque el swap solo haya tocado 1-2 políticas. `_proxy_delta()` evita eso:
  por cada evento tocado en el swap, suma k_i/(2m) -- el mismo término de
  grado normalizado que ya aparece dentro de la fórmula real de Q -- usando
  el grado que ese nodo tenía en el ÚLTIMO grafo construido (lectura O(1)
  cacheada por NetworkX; eventos nuevos sin nodo aún usan el grado promedio
  del grafo, también O(1)). Es decir: no es Q, es una estimación de "qué tan
  estructuralmente importante era lo que se acaba de tocar", calculada en
  O(k) (k = eventos del swap) en vez de O(N+E). No dispara la reconstrucción
  por sí sola todavía -- se acumula en paralelo (`stress_proxy`, ver
  get_stats()) para poder compararla contra la real y calibrar su umbral.

Uso:
    monitor = ModularityMonitor(
        evaluator=ce,
        graph_builder=gb,
        community_detector=cd,
        threshold=0.5,
    )
    # Después de cada hot-swap:
    monitor.record_swap()

    # O conectar al EventBus para hacerlo automáticamente:
    bus.subscribe("politica_actualizada", monitor.on_policy_event)
    bus.subscribe("politica_agregada",    monitor.on_policy_event)
    bus.subscribe("politica_eliminada",   monitor.on_policy_event)
"""

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
    """
    Rastrea la salud de la estructura comunitaria y dispara reconstrucción
    automática cuando el estrés acumulado supera el umbral configurado.
    """

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
        # Si True, cada reconstrucción de Nivel 4 corre audit_and_mitigate()
        # antes de reconstruir los índices del evaluador, para que cualquier
        # redundancia/conflicto introducido por reconfiguraciones dinámicas
        # desde la última reconstrucción quede limpio automáticamente.
        self.audit_policies       = audit_policies
        self.conflict_priority    = conflict_priority
        self.last_audit_report    = None

        self._stress: float          = 0.0
        self._stress_proxy: float    = 0.0
        self._q_current: float       = 0.0
        self._swap_count: int        = 0
        self._rebuilds: int          = 0
        self._rebuild_lock           = threading.Lock()
        self._rebuilding: bool       = False

        # Umbral proxy recalibrado: None hasta el primer disparo real (aún
        # no hay ciclo previo del que aprender). Se actualiza en el instante
        # exacto de cada disparo real (ver record_swap), tomando el valor que
        # stress_proxy tenía justo ahí -- así el umbral del siguiente ciclo
        # ya viene ajustado al tamaño de grafo vigente en ese momento, sin
        # ningún cálculo extra de Q.
        self._threshold_proxy: Optional[float] = None

        # Historial por swap (real vs. proxy + costo de cada uno), para poder
        # comparar ambas métricas después de una corrida — ver record_swap().
        self.history: List[Dict] = []

        # Calcular Q inicial si ya hay grafo
        if graph_builder.graph is not None:
            self._q_current = self._compute_modularity(graph_builder.graph)

        # Registrar automáticamente en el evaluador
        evaluator.register_policy_change_hook(self.record_swap)

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def record_swap(self, policy: Optional[Policy] = None) -> float:
        """
        Registra que ocurrió un hot-swap.  Recalcula Q con la partición actual
        y acumula el delta al índice de estrés real; en paralelo, calcula la
        aproximación barata (`_proxy_delta`) sobre la política afectada y la
        acumula por separado en `stress_proxy` — no reemplaza al disparador
        real todavía, solo corre al lado para poder compararlas.

        Args:
            policy: la política afectada por este swap (para la aproximación
                barata). Si no se provee, la aproximación no tiene información
                que usar y contribuye 0 para este swap.

        Returns:
            El delta de modularidad |ΔQ| real calculado para este swap.
        """
        if self.graph_builder.graph is None:
            return 0.0

        t0 = time.perf_counter()
        q_new = self._compute_modularity(self.graph_builder.graph)
        t_real = time.perf_counter() - t0
        delta = abs(q_new - self._q_current)
        self._q_current = q_new
        self._stress   += delta
        self._swap_count += 1

        t0 = time.perf_counter()
        delta_proxy = self._proxy_delta(policy)
        t_proxy = time.perf_counter() - t0
        self._stress_proxy += delta_proxy

        # Umbral proxy vigente ANTES de una posible recalibración por este
        # mismo swap (ver más abajo) -- así el historial refleja con qué
        # umbral se estaba comparando en el momento, no el que quedó después.
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

        if self._stress >= self.threshold and not self._rebuilding:
            log.info(
                "Estrés %.4f ≥ umbral %.4f — iniciando reconstrucción de grafo en segundo plano",
                self._stress, self.threshold,
            )
            # Recalibrar el umbral proxy en el instante exacto del disparo
            # real, usando lo que stress_proxy ya acumuló hasta aquí -- sin
            # ningún cálculo de Q adicional. Queda listo para el ciclo
            # siguiente; el de este ciclo (el que se registró arriba) no se
            # toca retroactivamente.
            self._threshold_proxy = self._stress_proxy
            self._trigger_rebuild()

        return delta

    def _proxy_delta(self, policy: Optional[Policy]) -> float:
        """
        Aproximación barata de cuánto "pesó" este swap, sin recalcular Q.

        Reutiliza el término k_i/(2m) que ya aparece dentro de la fórmula
        real de modularidad: por cada evento de la política tocada, si ese
        evento ya es un nodo del último grafo conocido, lee su grado (O(1),
        cacheado por NetworkX); si es un evento nuevo (sin nodo todavía),
        usa el grado promedio del grafo como estimado. Un nodo muy conectado
        pesa más en Q, así que tocarlo debería costar más en esta
        aproximación también — sin tocar el grafo completo. Costo: O(k),
        k = número de eventos de la política afectada (normalmente 1-2),
        en vez de O(N+E) de `_compute_modularity`.
        """
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
            k = graph.degree(event.event_id) if event.event_id in graph else avg_degree
            total += k / (2 * m)
        return total

    async def on_policy_event(self, event: DomainEvent) -> None:
        """
        Handler para EventBus.  Registra un swap cuando llega cualquier evento
        de cambio de política (agregada / eliminada / actualizada).
        """
        policy_id = getattr(event, "policy_id", None)
        policy = self.evaluator.policies.get(policy_id) if policy_id else None
        self.record_swap(policy)

    def get_stats(self) -> dict:
        """Devuelve estadísticas actuales del monitor."""
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
        """Reinicia los índices de estrés (real y proxy), llamado automáticamente tras reconstrucción."""
        self._stress = 0.0
        self._stress_proxy = 0.0

    # ------------------------------------------------------------------
    # Reconstrucción en segundo plano
    # ------------------------------------------------------------------

    def _trigger_rebuild(self) -> None:
        """Lanza la reconstrucción completa del grafo en un hilo daemon."""
        with self._rebuild_lock:
            if self._rebuilding:
                return
            self._rebuilding = True

        t = threading.Thread(target=self._rebuild_worker, daemon=True, name="apex-graph-rebuild")
        t.start()

    def _rebuild_worker(self) -> None:
        """
        Worker de reconstrucción (hilo separado).

        Flujo:
          1. Reconstruye el grafo de eventos con GraphBuilder.
          2. Detecta nuevas comunidades con CommunityDetector (Louvain).
          3. Mapea políticas a las nuevas comunidades.
          4. Aplica Atomic Swap sobre el evaluador.
          5. Recalcula Q y resetea el estrés.
        """
        try:
            log.info("[ModularityMonitor] Reconstruyendo grafo…")
            policies = list(self.evaluator.policies.values())

            # Reconstruir grafo
            graph = self.graph_builder.build_event_graph(
                policies, similarity_threshold=self.similarity_threshold
            )

            # Detectar comunidades
            event_communities, event_to_community = self.community_detector.detect_communities(graph)

            # Construir mapa event_id → policy_id
            event_to_policy = {}
            for policy in policies:
                for event in policy.events:
                    event_to_policy[event.event_id] = policy.policy_id

            # Mapear políticas a comunidades
            policies_by_community = self.community_detector.map_policies_to_communities(
                event_to_policy, event_to_community
            )

            # Auditoría de redundancia/conflictos (Nivel 4): se aplica ANTES
            # de construir los índices finales del evaluador, para que estos
            # nazcan ya limpios en vez de requerir una segunda tanda de
            # hot-swaps inmediatamente después.
            if self.audit_policies:
                policies, policies_by_community, self.last_audit_report = audit_and_mitigate(
                    policies, policies_by_community, conflict_priority=self.conflict_priority
                )
                log.info("[ModularityMonitor] Auditoría de políticas: %s", self.last_audit_report.summary())

            # Atomic swap en el evaluador
            self.evaluator.run(policies, policies_by_community)

            # Recalcular Q y resetear estrés
            self._q_current = self._compute_modularity(graph)
            self.reset_stress()
            self._rebuilds += 1

            log.info(
                "[ModularityMonitor] Reconstrucción completada — Q=%.4f  rebuilds=%d",
                self._q_current, self._rebuilds,
            )

        except Exception as exc:
            log.error("[ModularityMonitor] Error en reconstrucción: %s", exc, exc_info=True)
        finally:
            with self._rebuild_lock:
                self._rebuilding = False

    # ------------------------------------------------------------------
    # Cálculo de modularidad
    # ------------------------------------------------------------------

    def _compute_modularity(self, graph: nx.Graph) -> float:
        """
        Calcula la modularidad Q de la partición comunitaria actual.
        Devuelve 0.0 si el grafo no tiene aristas (Q indefinida).
        """
        if graph.number_of_edges() == 0:
            return 0.0

        # Construir partición desde policies_by_community
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

        # Los nodos de políticas ya desactivadas (sin eventos, ver RF-08 /
        # validate_integrity) siguen en `graph` hasta la próxima reconstrucción
        # completa, pero ya no pertenecen a ninguna comunidad viva. Pasarle a
        # modularity() el grafo COMPLETO cuando la partición ya no lo cubre
        # entero lanza NotAPartition en cada llamada -- silenciosamente
        # atrapado por el except de abajo, dejando Q clavada en 0.0 desde la
        # primera política que se desactiva. Se restringe el universo a los
        # nodos realmente cubiertos por la partición actual.
        subgraph = graph.subgraph(covered) if len(covered) < graph.number_of_nodes() else graph

        try:
            return modularity(subgraph, communities, weight="weight")
        except Exception:
            return 0.0
