from typing import List, Dict, Set, Tuple, Callable, Optional
from collections import defaultdict
from devac.models import Policy, AccessRequest, Event
from devac.matcher import EventMatcher
from devac.graph import GraphBuilder
from devac.interval_tree import IntervalTree, _is_time_format
import time



class CommunityPolicyEvaluator:
    """
    Evaluador basado en comunidades con RUTEO por atributos + Gestión Dinámica.

    - índices precalculados (value_to_comms, freq)
    - ruteo eficiente por atributos
    - add_policy(), remove_policy(), update_policy()
    - reconstrucción incremental de índices
    - soporte para wildcards
    - pesos de entropía dinámicos
    - mitigación de Race Conditions mediante Atomic Swaps (Thread-Safe)
    """

    ATTRS = ["rol", "recurso", "ubicacion", "rango_horario", "accion"]

    def __init__(self, policies: List[Policy] = [], policies_by_community: Dict[str, List[str]] = {}):
        self.policies: Dict[str, Policy] = {p.policy_id: p for p in policies}
        self.policies_by_community = policies_by_community

        # Estructuras precalculadas
        self.value_to_comms: Dict[str, Dict[str, Set[str]]] = {attr: defaultdict(set) for attr in self.ATTRS}
        self.freq: Dict[str, Dict[str, Dict[str, float]]] = {}  # comm→attr->value->freq
        self.events_by_comm: Dict[str, List[Event]] = {}

        # Interval tree para rango_horario con formato HH:MM
        self.interval_tree: IntervalTree = IntervalTree()

        # Pesos de entropía
        self.attribute_weights: Dict[str, float] = {}

        # Callbacks registrados para mutaciones de política (add/update/remove)
        self._on_policy_change: List[Callable[[], None]] = []

    # ------------------------------------------------------------------
    # HOOKS: observadores de mutaciones de política
    # ------------------------------------------------------------------
    def register_policy_change_hook(self, fn: Callable[[Optional[Policy]], None]) -> None:
        """
        Registra un callback que se invoca después de cualquier mutación
        de política (add, update, remove). Recibe la Policy afectada — la
        versión nueva en add, la versión PRE-swap (antes de sobrescribir)
        en update, la versión recién retirada en remove — o None si no hay
        una política puntual asociada al cambio. En update se pasa la
        versión anterior a propósito: si la regla vació la política
        (events -> []), la versión nueva ya no tiene nada que un observador
        pueda medir; la anterior sí conserva los event_id que siguen siendo
        nodos reales del grafo.
        """
        self._on_policy_change.append(fn)

    def _notify_policy_change(self, policy: Optional[Policy] = None) -> None:
        for fn in self._on_policy_change:
            fn(policy)

    # ------------------------------------------------------------------
    # OFFLINE: construir índices rápidos por comunidad
    # ------------------------------------------------------------------
    def run(self, policies: List[Policy], policies_by_community: Dict[str, List[str]]):
        """
        Construye todos los índices desde cero.
        Usar cuando se inicializa el sistema o se reconstruye completamente.
        """
        self.policies = {p.policy_id: p for p in policies}
        self.policies_by_community = policies_by_community

        # Calcular pesos dinámicos de atributos (mejora de V2)
        self.attribute_weights = GraphBuilder.calculate_attribute_weights(policies)

        # Reconstruir índices completamente
        self._rebuild_all_indices()

    def _rebuild_all_indices(self):
        """
        Reconstruye todos los índices desde cero de forma segura (Atomic Swap).
        """
        # 1. Crear estructuras temporales (Shadow Copies)
        new_value_to_comms = {attr: defaultdict(set) for attr in self.ATTRS}
        new_freq = {comm: {attr: defaultdict(int) for attr in self.ATTRS} for comm in self.policies_by_community}
        new_events_by_comm = {comm: [] for comm in self.policies_by_community}
        new_interval_tree = IntervalTree()

        # 2. Llenar las estructuras temporales
        for comm, pids in self.policies_by_community.items():
            events = []

            for pid in pids:
                policy = self.policies.get(pid)
                if not policy:
                    continue

                for e in policy.events:
                    # Normalizar valores (solo strings; los dict DSL no se tocan)
                    if isinstance(e.subject.value, str): e.subject.value = e.subject.value.strip().lower()
                    if isinstance(e.asset.value,   str): e.asset.value   = e.asset.value.strip().lower()
                    if isinstance(e.space.value,   str): e.space.value   = e.space.value.strip().lower()
                    if isinstance(e.time.value,    str): e.time.value    = e.time.value.strip().lower()
                    if isinstance(e.action.value,  str): e.action.value  = e.action.value.strip().lower()

                    events.append(e)

                    # Expandir DSL dict y agregar todos los valores al índice invertido
                    def _idx(attr_key, val):
                        for expanded in EventMatcher.expand_value(val, attr_key):
                            new_value_to_comms[attr_key][expanded].add(comm)
                            new_freq[comm][attr_key][expanded] += 1

                    _idx("rol",          e.subject.value)
                    _idx("recurso",      e.asset.value)
                    _idx("ubicacion",    e.space.value)
                    _idx("accion",       e.action.value)

                    # rango_horario: string exacto/in → índice invertido
                    #                {"range":[...]}  → interval tree
                    time_val = e.time.value
                    if isinstance(time_val, dict) and "range" in time_val:
                        bounds = time_val["range"]
                        if len(bounds) == 2:
                            new_interval_tree.insert(bounds[0], bounds[1], comm, e.event_id)
                    else:
                        _idx("rango_horario", time_val)

            new_events_by_comm[comm] = events

        # 3. Convertir frecuencias absolutas a relativas
        for comm, attrs in new_freq.items():
            total_events = float(len(new_events_by_comm[comm])) or 1.0
            for attr, values in attrs.items():
                for val in values:
                    values[val] /= total_events

        # 4. INTERCAMBIO ATÓMICO (Thread-Safe)
        self.events_by_comm  = new_events_by_comm
        self.freq            = new_freq
        self.value_to_comms  = new_value_to_comms
        self.interval_tree   = new_interval_tree

    def _rebuild_community_indices(self, community_id: str):
        """
        Reconstruye índices SOLO para una comunidad específica usando Atomic Swap.
        """
        if community_id not in self.policies_by_community:
            return

        pids = self.policies_by_community[community_id]
        
        # 1. Crear estructuras temporales locales para esta comunidad
        new_comm_freq = {attr: defaultdict(int) for attr in self.ATTRS}
        events = []

        # 2. Reconstruir eventos y frecuencias
        for pid in pids:
            policy = self.policies.get(pid)
            if not policy:
                continue

            for e in policy.events:
                # Normalizar valores (solo strings)
                if isinstance(e.subject.value, str): e.subject.value = e.subject.value.strip().lower()
                if isinstance(e.asset.value,   str): e.asset.value   = e.asset.value.strip().lower()
                if isinstance(e.space.value,   str): e.space.value   = e.space.value.strip().lower()
                if isinstance(e.time.value,    str): e.time.value    = e.time.value.strip().lower()
                if isinstance(e.action.value,  str): e.action.value  = e.action.value.strip().lower()

                events.append(e)

                # Expandir DSL dict y agregar todos los valores a frecuencias
                def _freq(attr_key, val):
                    for expanded in EventMatcher.expand_value(val, attr_key):
                        new_comm_freq[attr_key][expanded] += 1

                _freq("rol",     e.subject.value)
                _freq("recurso", e.asset.value)
                _freq("ubicacion", e.space.value)
                _freq("accion",  e.action.value)

                # rango_horario
                time_val = e.time.value
                if isinstance(time_val, dict) and "range" in time_val:
                    bounds = time_val["range"]
                    if len(bounds) == 2:
                        pass  # handled below by global interval_tree rebuild
                else:
                    _freq("rango_horario", time_val)

        # Convertir frecuencias absolutas a relativas en el temporal
        total_events = float(len(events)) or 1.0
        for attr, values in new_comm_freq.items():
            for val in values:
                values[val] /= total_events

        # Intercambio atómico de eventos y frecuencia de la comunidad
        self.events_by_comm[community_id] = events
        self.freq[community_id] = new_comm_freq

        # 3. Reconstruir value_to_comms e interval_tree globales en temporales
        new_value_to_comms = {attr: defaultdict(set) for attr in self.ATTRS}
        new_interval_tree  = IntervalTree()

        for comm, comm_events in self.events_by_comm.items():
            for e in comm_events:
                def _vidx(attr_key, val):
                    for expanded in EventMatcher.expand_value(val, attr_key):
                        new_value_to_comms[attr_key][expanded].add(comm)
                _vidx("rol",      e.subject.value)
                _vidx("recurso",  e.asset.value)
                _vidx("ubicacion",e.space.value)
                _vidx("accion",   e.action.value)

                time_val = e.time.value
                if isinstance(time_val, dict) and "range" in time_val:
                    bounds = time_val["range"]
                    if len(bounds) == 2:
                        new_interval_tree.insert(bounds[0], bounds[1], comm, e.event_id)
                else:
                    _vidx("rango_horario", time_val)

        # 4. INTERCAMBIO ATÓMICO FINAL
        self.value_to_comms = new_value_to_comms
        self.interval_tree  = new_interval_tree

    # ------------------------------------------------------------------
    # GESTIÓN DINÁMICA: Añadir política
    # ------------------------------------------------------------------
    def add_policy(self, policy: Policy, auto_assign_community: bool = True):
        # Normalizar eventos de la nueva política (solo strings)
        for e in policy.events:
            if isinstance(e.subject.value, str): e.subject.value = e.subject.value.strip().lower()
            if isinstance(e.asset.value,   str): e.asset.value   = e.asset.value.strip().lower()
            if isinstance(e.space.value,   str): e.space.value   = e.space.value.strip().lower()
            if isinstance(e.time.value,    str): e.time.value    = e.time.value.strip().lower()
            if isinstance(e.action.value,  str): e.action.value  = e.action.value.strip().lower()

        # Añadir a diccionario de políticas
        self.policies[policy.policy_id] = policy

        if auto_assign_community:
            best_community = self._find_best_community_for_policy(policy)

            if best_community is None:
                print(f"[WARNING] No se encontró comunidad adecuada para política {policy.policy_id}")
                return

            self.assign_policy_to_community(policy.policy_id, best_community)

        self._notify_policy_change(policy)

    def assign_policy_to_community(self, policy_id: str, community_id: str):
        if community_id not in self.policies_by_community:
            self.policies_by_community[community_id] = []
            self.freq[community_id] = {attr: defaultdict(int) for attr in self.ATTRS}
            self.events_by_comm[community_id] = []

        if policy_id not in self.policies_by_community[community_id]:
            self.policies_by_community[community_id].append(policy_id)

        self._rebuild_community_indices(community_id)

    def _find_best_community_for_policy(self, policy: Policy) -> str:
        best_community = None
        best_score = -1

        for community_id, policy_ids in self.policies_by_community.items():
            community_score = 0.0

            for new_event in policy.events:
                for existing_event in self.events_by_comm.get(community_id, []):
                    score = self._weighted_similarity_score(new_event, existing_event)
                    community_score += score

            if community_score > best_score:
                best_score = community_score
                best_community = community_id

        return best_community

    def _weighted_similarity_score(self, e1: Event, e2: Event) -> float:
        score = 0.0

        def expanded(comp, attr):
            return set(EventMatcher.expand_value(comp.value, attr))

        def overlap(c1, c2, attr):
            s1 = expanded(c1, attr)
            s2 = expanded(c2, attr)
            return bool(s1 & s2) or "*" in s1 or "*" in s2

        if overlap(e1.subject, e2.subject, "rol"):
            score += self.attribute_weights.get("rol", 0.2)
        if overlap(e1.asset, e2.asset, "recurso"):
            score += self.attribute_weights.get("recurso", 0.2)
        if overlap(e1.space, e2.space, "ubicacion"):
            score += self.attribute_weights.get("ubicacion", 0.2)
        if overlap(e1.time, e2.time, "rango_horario"):
            score += self.attribute_weights.get("rango_horario", 0.2)
        if overlap(e1.action, e2.action, "accion"):
            score += self.attribute_weights.get("accion", 0.2)

        return score

    # ------------------------------------------------------------------
    # GESTIÓN DINÁMICA: Eliminar política
    # ------------------------------------------------------------------
    def remove_policy(self, policy_id: str):
        if policy_id not in self.policies:
            print(f"[WARNING] Política {policy_id} no existe")
            return

        removed_policy = self.policies.pop(policy_id, None)

        for community_id, policy_ids in self.policies_by_community.items():
            if policy_id in policy_ids:
                policy_ids.remove(policy_id)
                self._rebuild_community_indices(community_id)
                print(f"[INFO] Política {policy_id} eliminada de comunidad {community_id}")
                self._notify_policy_change(removed_policy)
                return

        print(f"[WARNING] Política {policy_id} no estaba asignada a ninguna comunidad")

    # ------------------------------------------------------------------
    # GESTIÓN DINÁMICA: Actualizar política
    # ------------------------------------------------------------------
    def update_policy(self, updated_policy: Policy):
        pid = updated_policy.policy_id

        if pid not in self.policies:
            print(f"[WARNING] Política {pid} no existe, usar add_policy() en su lugar")
            return

        for e in updated_policy.events:
            if isinstance(e.subject.value, str): e.subject.value = e.subject.value.strip().lower()
            if isinstance(e.asset.value,   str): e.asset.value   = e.asset.value.strip().lower()
            if isinstance(e.space.value,   str): e.space.value   = e.space.value.strip().lower()
            if isinstance(e.time.value,    str): e.time.value    = e.time.value.strip().lower()
            if isinstance(e.action.value,  str): e.action.value  = e.action.value.strip().lower()

        # Se guarda la versión PRE-swap antes de sobrescribir: es la que un
        # observador barato (ver ModularityMonitor._proxy_delta) necesita
        # para pesar lo que se perdió -- p. ej. al vaciar una política
        # (events -> []), la versión nueva ya no tiene nada que medir; la
        # vieja sí tiene los event_id que aún son nodos reales del grafo.
        previous_policy = self.policies.get(pid)
        self.policies[pid] = updated_policy

        for community_id, policy_ids in self.policies_by_community.items():
            if pid in policy_ids:
                self._rebuild_community_indices(community_id)
                print(f"[INFO] Política {pid} actualizada en comunidad {community_id}")
                self._notify_policy_change(previous_policy)
                return

        print(f"[WARNING] Política {pid} no estaba asignada a ninguna comunidad")

    # ------------------------------------------------------------------
    # GESTIÓN DINÁMICA: Mover política entre comunidades
    # ------------------------------------------------------------------
    def move_policy_to_community(self, policy_id: str, target_community_id: str):
        if policy_id not in self.policies:
            print(f"[WARNING] Política {policy_id} no existe")
            return

        source_community = None
        for community_id, policy_ids in self.policies_by_community.items():
            if policy_id in policy_ids:
                source_community = community_id
                policy_ids.remove(policy_id)
                break

        if target_community_id not in self.policies_by_community:
            self.policies_by_community[target_community_id] = []
            self.freq[target_community_id] = {attr: defaultdict(int) for attr in self.ATTRS}
            self.events_by_comm[target_community_id] = []

        self.policies_by_community[target_community_id].append(policy_id)

        if source_community:
            self._rebuild_community_indices(source_community)
        self._rebuild_community_indices(target_community_id)

        print(f"[INFO] Política {policy_id} movida de {source_community} a {target_community_id}")

    # ------------------------------------------------------------------
    # ONLINE: identificar comunidades candidatas
    # ------------------------------------------------------------------
    def _candidate_communities(self, request: AccessRequest) -> Set[str]:
        time_val = request.time.value.strip().lower()

        vals = {
            "rol":      request.subject.value.strip().lower(),
            "recurso":  request.asset.value.strip().lower(),
            "ubicacion":request.space.value.strip().lower(),
            "accion":   request.action.value.strip().lower(),
        }

        candidate_sets = []

        # 4 atributos sin rango_horario → índice invertido normal
        for attr, val in vals.items():
            attr_index = self.value_to_comms.get(attr, {})
            exact    = attr_index.get(val, set())
            wildcard = attr_index.get("*", set())
            combined = exact | wildcard
            if not combined:
                return set()
            candidate_sets.append(combined)

        # rango_horario: si es HH:MM usa interval tree + índice;
        # si es categórico ("mediana", etc.) usa solo índice invertido
        rh_index   = self.value_to_comms.get("rango_horario", {})
        rh_exact   = rh_index.get(time_val, set())
        rh_wildcard= rh_index.get("*", set())

        if _is_time_format(time_val):
            rh_range = self.interval_tree.query(time_val)
            rh_combined = rh_exact | rh_wildcard | rh_range
        else:
            rh_combined = rh_exact | rh_wildcard

        if not rh_combined:
            return set()
        candidate_sets.append(rh_combined)

        return set.intersection(*candidate_sets)

    # ------------------------------------------------------------------
    # ONLINE: score por comunidad
    # ------------------------------------------------------------------
    def _score_comm(self, comm: str, req_vals: Dict[str, str]) -> float:
        s = 0.0
        for attr in self.ATTRS:
            s += self.freq.get(comm, {}).get(attr, {}).get(req_vals[attr], 0.0)
        return s

    # ------------------------------------------------------------------
    # ONLINE: evaluación por comunidad
    # ------------------------------------------------------------------
    def evaluate(self, request: AccessRequest) -> dict:
        start = time.time()

        # Normalizar request
        request.subject.value = request.subject.value.strip().lower()
        request.asset.value = request.asset.value.strip().lower()
        request.space.value = request.space.value.strip().lower()
        request.time.value = request.time.value.strip().lower()
        request.action.value = request.action.value.strip().lower()

        req_vals = {
            "rol": request.subject.value,
            "recurso": request.asset.value,
            "ubicacion": request.space.value,
            "rango_horario": request.time.value,
            "accion": request.action.value,
        }

        # 1. Comunidades candidatas
        candidates = self._candidate_communities(request)

        if not candidates:
            eval_time = time.time() - start
            return {"result": "deny", "policy_id": None, "event_id": None, "eval_time": eval_time, "steps": 0}

        # 2. Ordenar por score de frecuencia
        sorted_candidates = sorted(
            candidates,
            key=lambda c: self._score_comm(c, req_vals),
            reverse=True,
        )

        steps = 0
        for comm in sorted_candidates:
            for pid in self.policies_by_community.get(comm, []):
                policy = self.policies.get(pid)
                if not policy:
                    continue
                for event in policy.events:
                    steps += 1
                    if EventMatcher.match_event(event, request):
                        eval_time = time.time() - start
                        return {
                            "result": policy.effect,
                            "policy_id": policy.policy_id,
                            "event_id": event.event_id,
                            "eval_time": eval_time,
                            "steps": steps,
                        }

        # Agotó todas las comunidades candidatas sin encontrar match
        eval_time = time.time() - start
        return {"result": "deny", "policy_id": None, "event_id": None, "eval_time": eval_time, "steps": steps}

    # ------------------------------------------------------------------
    # UTILIDADES: Estadísticas y debugging
    # ------------------------------------------------------------------
    def get_statistics(self) -> dict:
        stats = {
            "total_policies": len(self.policies),
            "total_communities": len(self.policies_by_community),
            "communities": {}
        }

        for comm_id, policy_ids in self.policies_by_community.items():
            stats["communities"][comm_id] = {
                "num_policies": len(policy_ids),
                "num_events": len(self.events_by_comm.get(comm_id, [])),
                "top_values": {
                    attr: sorted(
                        self.freq.get(comm_id, {}).get(attr, {}).items(),
                        key=lambda x: x[1],
                        reverse=True
                    )[:5]
                    for attr in self.ATTRS
                }
            }

        return stats

    def validate_integrity(self) -> List[str]:
        issues = []

        for comm_id, policy_ids in self.policies_by_community.items():
            for pid in policy_ids:
                if pid not in self.policies:
                    issues.append(f"Política {pid} en comunidad {comm_id} no existe en self.policies")

        assigned_pids = set()
        for policy_ids in self.policies_by_community.values():
            assigned_pids.update(policy_ids)

        for pid in self.policies:
            if pid not in assigned_pids:
                # Una política sin eventos (p. ej. tras retirarse todos por
                # redundancia, o vaciada por una regla de reconfiguración) es
                # inactiva por definición: no aporta nodos al grafo, así que
                # no pertenecer a ninguna comunidad es el estado esperado,
                # no una inconsistencia.
                if len(self.policies[pid].events) == 0:
                    continue
                issues.append(f"Política {pid} no está asignada a ninguna comunidad")

        for comm_id, events in self.events_by_comm.items():
            expected_count = sum(
                len(self.policies[pid].events)
                for pid in self.policies_by_community.get(comm_id, [])
                if pid in self.policies
            )
            if len(events) != expected_count:
                issues.append(f"Comunidad {comm_id}: eventos={len(events)}, esperados={expected_count}")

        return issues

