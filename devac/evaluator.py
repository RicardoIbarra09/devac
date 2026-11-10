from typing import List, Dict, Set, Tuple, Callable, Optional
from collections import defaultdict
from dataclasses import dataclass, replace
from devac.models import Policy, AccessRequest, Event
from devac.matcher import EventMatcher
from devac.graph import GraphBuilder
from devac.interval_tree import IntervalTree, _is_time_format
import time


PolicyChangeHook = Callable[[Optional[Policy], Optional[Policy], Dict[str, str]], None]


# todo lo que lee evaluate(). evaluate() corre sin candado: toma self._state una vez al empezar y
# solo usa esa instancia. una escritura nunca modifica un estado publicado; arma uno nuevo
# (copy-on-write) y lo publica con una sola asignacion a self._state
@dataclass(frozen=True, slots=True)
class _State:
    policies: Dict[str, Policy]
    policies_by_community: Dict[object, List[str]]
    policy_community: Dict[str, object]  # politica -> comunidad
    # frecuencia relativa de cada valor por comunidad
    freq: Dict[object, Dict[str, Dict[str, float]]]
    events_by_comm: Dict[object, List[Event]]
    event_policy_effect: Dict[str, str]  # event_id -> allow/deny de su politica
    # attr -> valor -> comunidades que lo tienen; _deny igual pero solo con eventos deny
    value_to_comms: Dict[str, Dict[str, Set[object]]]
    value_to_comms_deny: Dict[str, Dict[str, Set[object]]]
    interval_tree: IntervalTree  # para rangos HH:MM
    interval_tree_deny: IntervalTree
    # eventos que la auditoria encontro cubiertos por otro del mismo efecto:
    # event_id -> (su politica, la politica que lo cubre). siguen en su politica, pero no se
    # indexan ni se comparan mientras la que los cubre no cambie
    excluded: Dict[str, Tuple[str, str]]
    attribute_weights: Dict[str, float]


def _read_only(name: str) -> property:
    return property(lambda self: getattr(self._state, name))


class CommunityPolicyEvaluator:
    # nombres internos de las 5 dimensiones
    ATTRS = ["rol", "recurso", "ubicacion", "rango_horario", "accion"]

    # los atributos de siempre, de solo lectura sobre el estado publicado
    policies = _read_only("policies")
    policies_by_community = _read_only("policies_by_community")
    policy_community = _read_only("policy_community")
    freq = _read_only("freq")
    events_by_comm = _read_only("events_by_comm")
    event_policy_effect = _read_only("event_policy_effect")
    value_to_comms = _read_only("value_to_comms")
    value_to_comms_deny = _read_only("value_to_comms_deny")
    interval_tree = _read_only("interval_tree")
    interval_tree_deny = _read_only("interval_tree_deny")
    excluded = _read_only("excluded")
    attribute_weights = _read_only("attribute_weights")

    def __init__(self, policies: List[Policy] = [], policies_by_community: Dict[str, List[str]] = {}):
        self._state = _State(
            policies={p.policy_id: p for p in policies},
            policies_by_community=policies_by_community,
            policy_community=self._membership(policies_by_community),
            freq={},
            events_by_comm={},
            event_policy_effect={},
            value_to_comms={attr: defaultdict(set) for attr in self.ATTRS},
            value_to_comms_deny={attr: defaultdict(set) for attr in self.ATTRS},
            interval_tree=IntervalTree(),
            interval_tree_deny=IntervalTree(),
            excluded={},
            attribute_weights={},
        )
        # el estado que va armando la escritura en curso, para que most_similar_community vea lo que
        # ya cambio (como antes, cuando cada paso se publicaba). evaluate() nunca lo lee
        self._draft: Optional[_State] = None

        # elige la comunidad de una politica nueva; None = abrir una comunidad para ella.
        # lo instala ModularityMonitor (ganancia de modularidad); sin el se usa la similitud por atributos
        self.community_assigner: Optional[Callable[[Policy], Optional[object]]] = None

        # callbacks que se llaman al cambiar una politica, con (anterior, nueva, liberados):
        # alta (None, p), baja (p, None), cambio (vieja, nueva), cambio de comunidad (p, p).
        # liberados: event_id -> politica, eventos de otras politicas que volvieron a los indices
        # porque la que los cubria cambio o se borro
        self._on_policy_change: List[PolicyChangeHook] = []

    def register_policy_change_hook(self, fn: "PolicyChangeHook") -> None:
        self._on_policy_change.append(fn)

    def _notify_policy_change(self, old: Optional[Policy], new: Optional[Policy],
                              released: Optional[Dict[str, str]] = None) -> None:
        for fn in self._on_policy_change:
            fn(old, new, released or {})

    @staticmethod
    def _membership(policies_by_community: Dict[str, List[str]]) -> Dict[str, object]:
        return {pid: comm for comm, pids in policies_by_community.items() for pid in pids}

    def _current(self) -> _State:
        return self._draft if self._draft is not None else self._state

    # construye todo desde cero
    def run(self, policies: List[Policy], policies_by_community: Dict[str, List[str]],
            excluded: Optional[Dict[str, Tuple[str, str]]] = None):
        st = replace(
            self._state,
            policies={p.policy_id: p for p in policies},
            policies_by_community=policies_by_community,
            policy_community=self._membership(policies_by_community),
            excluded=dict(excluded or {}),
            attribute_weights=GraphBuilder.calculate_attribute_weights(policies),  # ignora las inactivas
        )
        # los lectores ven la particion vieja con sus indices o la nueva con los suyos, nunca mezcladas
        self._state = self._rebuild_all_indices(st)

    def _rebuild_all_indices(self, st: _State) -> _State:
        # se arma todo en estructuras nuevas
        new_value_to_comms = {attr: defaultdict(set) for attr in self.ATTRS}
        new_value_to_comms_deny = {attr: defaultdict(set) for attr in self.ATTRS}
        new_freq = {comm: {attr: defaultdict(int) for attr in self.ATTRS} for comm in st.policies_by_community}
        new_events_by_comm = {comm: [] for comm in st.policies_by_community}
        new_event_policy_effect: Dict[str, str] = {}
        new_interval_tree = IntervalTree()
        new_interval_tree_deny = IntervalTree()

        for comm, pids in st.policies_by_community.items():
            events = []

            for pid in pids:
                policy = st.policies.get(pid)
                if not policy or not policy.active:
                    continue
                is_deny = policy.effect == "deny"

                # normaliza a minusculas antes de indexar
                for e in policy.events:
                    if isinstance(e.subject.value, str): e.subject.value = e.subject.value.strip().lower()
                    if isinstance(e.asset.value,   str): e.asset.value   = e.asset.value.strip().lower()
                    if isinstance(e.space.value,   str): e.space.value   = e.space.value.strip().lower()
                    if isinstance(e.time.value,    str): e.time.value    = e.time.value.strip().lower()
                    if isinstance(e.action.value,  str): e.action.value  = e.action.value.strip().lower()
                    if e.event_id in st.excluded:
                        continue

                    events.append(e)
                    new_event_policy_effect[e.event_id] = policy.effect

                    def _idx(attr_key, val):
                        for expanded in EventMatcher.expand_value(val, attr_key):
                            new_value_to_comms[attr_key][expanded].add(comm)
                            new_freq[comm][attr_key][expanded] += 1
                            if is_deny:
                                new_value_to_comms_deny[attr_key][expanded].add(comm)

                    _idx("rol",          e.subject.value)
                    _idx("recurso",      e.asset.value)
                    _idx("ubicacion",    e.space.value)
                    _idx("accion",       e.action.value)

                    time_val = e.time.value
                    if isinstance(time_val, dict) and "range" in time_val:
                        bounds = time_val["range"]
                        if len(bounds) == 2:
                            new_interval_tree.insert(bounds[0], bounds[1], comm, e.event_id)
                            if is_deny:
                                new_interval_tree_deny.insert(bounds[0], bounds[1], comm, e.event_id)
                    else:
                        _idx("rango_horario", time_val)

            new_events_by_comm[comm] = events

        for comm, attrs in new_freq.items():
            total_events = float(len(new_events_by_comm[comm])) or 1.0
            for attr, values in attrs.items():
                for val in values:
                    values[val] /= total_events

        return replace(
            st,
            events_by_comm=new_events_by_comm,
            freq=new_freq,
            value_to_comms=new_value_to_comms,
            value_to_comms_deny=new_value_to_comms_deny,
            event_policy_effect=new_event_policy_effect,
            interval_tree=new_interval_tree,
            interval_tree_deny=new_interval_tree_deny,
        )

    # reconstruye una sola comunidad tras un cambio; devuelve un estado nuevo, st no se toca
    def _rebuild_community_indices(self, st: _State, community_id: str) -> _State:
        if community_id not in st.policies_by_community:
            return st

        pids = st.policies_by_community[community_id]

        new_comm_freq = {attr: defaultdict(int) for attr in self.ATTRS}
        events = []

        # quita los eventos que tenia antes la comunidad
        old_ids = {e.event_id for e in st.events_by_comm.get(community_id, [])}
        event_policy_effect = {eid: eff for eid, eff in st.event_policy_effect.items() if eid not in old_ids}

        for pid in pids:
            policy = st.policies.get(pid)
            if not policy or not policy.active:
                continue

            for e in policy.events:
                if isinstance(e.subject.value, str): e.subject.value = e.subject.value.strip().lower()
                if isinstance(e.asset.value,   str): e.asset.value   = e.asset.value.strip().lower()
                if isinstance(e.space.value,   str): e.space.value   = e.space.value.strip().lower()
                if isinstance(e.time.value,    str): e.time.value    = e.time.value.strip().lower()
                if isinstance(e.action.value,  str): e.action.value  = e.action.value.strip().lower()
                if e.event_id in st.excluded:
                    continue

                events.append(e)
                event_policy_effect[e.event_id] = policy.effect

                def _freq(attr_key, val):
                    for expanded in EventMatcher.expand_value(val, attr_key):
                        new_comm_freq[attr_key][expanded] += 1

                _freq("rol",     e.subject.value)
                _freq("recurso", e.asset.value)
                _freq("ubicacion", e.space.value)
                _freq("accion",  e.action.value)

                time_val = e.time.value
                if isinstance(time_val, dict) and "range" in time_val:
                    bounds = time_val["range"]
                    if len(bounds) == 2:
                        pass
                else:
                    _freq("rango_horario", time_val)

        total_events = float(len(events)) or 1.0
        for attr, values in new_comm_freq.items():
            for val in values:
                values[val] /= total_events

        events_by_comm = {**st.events_by_comm, community_id: events}
        freq = {**st.freq, community_id: new_comm_freq}

        new_value_to_comms = {attr: defaultdict(set) for attr in self.ATTRS}
        new_value_to_comms_deny = {attr: defaultdict(set) for attr in self.ATTRS}
        new_interval_tree  = IntervalTree()
        new_interval_tree_deny = IntervalTree()

        # los indices globales si se rehacen completos
        for comm, comm_events in events_by_comm.items():
            for e in comm_events:
                is_deny = event_policy_effect.get(e.event_id) == "deny"

                def _vidx(attr_key, val):
                    for expanded in EventMatcher.expand_value(val, attr_key):
                        new_value_to_comms[attr_key][expanded].add(comm)
                        if is_deny:
                            new_value_to_comms_deny[attr_key][expanded].add(comm)
                _vidx("rol",      e.subject.value)
                _vidx("recurso",  e.asset.value)
                _vidx("ubicacion",e.space.value)
                _vidx("accion",   e.action.value)

                time_val = e.time.value
                if isinstance(time_val, dict) and "range" in time_val:
                    bounds = time_val["range"]
                    if len(bounds) == 2:
                        new_interval_tree.insert(bounds[0], bounds[1], comm, e.event_id)
                        if is_deny:
                            new_interval_tree_deny.insert(bounds[0], bounds[1], comm, e.event_id)
                else:
                    _vidx("rango_horario", time_val)

        return replace(
            st,
            events_by_comm=events_by_comm,
            freq=freq,
            event_policy_effect=event_policy_effect,
            value_to_comms=new_value_to_comms,
            value_to_comms_deny=new_value_to_comms_deny,
            interval_tree=new_interval_tree,
            interval_tree_deny=new_interval_tree_deny,
        )

    # agrega en caliente, sin reconstruir el grafo
    def add_policy(self, policy: Policy, auto_assign_community: bool = True):
        for e in policy.events:
            if isinstance(e.subject.value, str): e.subject.value = e.subject.value.strip().lower()
            if isinstance(e.asset.value,   str): e.asset.value   = e.asset.value.strip().lower()
            if isinstance(e.space.value,   str): e.space.value   = e.space.value.strip().lower()
            if isinstance(e.time.value,    str): e.time.value    = e.time.value.strip().lower()
            if isinstance(e.action.value,  str): e.action.value  = e.action.value.strip().lower()

        st = replace(self._state, policies={**self._state.policies, policy.policy_id: policy})

        if auto_assign_community and policy.active:
            best_community = self._choose_community(st, policy)
            if best_community is not None:
                st = self._assign(st, policy.policy_id, best_community)

        self._state = st
        self._notify_policy_change(None, policy)  # avisa al monitor, aunque quede sin comunidad

    def _choose_community(self, st: _State, policy: Policy):
        self._draft = st
        try:
            if self.community_assigner is not None:
                best_community = self.community_assigner(policy)
                # sin evento no hay nodo, no tiene caso abrirle comunidad
                if best_community is None and policy.events:
                    best_community = self._new_community_id(st)
                return best_community
            best_community = self.most_similar_community(policy)  # la comunidad mas parecida
            if best_community is None:
                best_community = self._new_community_id(st)  # todavia no hay ninguna
            return best_community
        finally:
            self._draft = None

    def assign_policy_to_community(self, policy_id: str, community_id: str):
        self._state = self._assign(self._state, policy_id, community_id)
        policy = self.policies.get(policy_id)
        self._notify_policy_change(policy, policy)

    def _assign(self, st: _State, policy_id: str, community_id: str) -> _State:
        pids = st.policies_by_community.get(community_id, [])
        if policy_id not in pids:
            pids = pids + [policy_id]
        st = replace(
            st,
            policies_by_community={**st.policies_by_community, community_id: pids},
            policy_community={**st.policy_community, policy_id: community_id},
        )
        return self._rebuild_community_indices(st, community_id)  # tambien crea su freq y events_by_comm

    @staticmethod
    def _new_community_id(st: _State):
        ids = list(st.policies_by_community)
        if all(isinstance(c, int) for c in ids):
            return max(ids, default=-1) + 1  # louvain numera con enteros
        n = len(ids)
        while f"c{n}" in st.policies_by_community:
            n += 1
        return f"c{n}"

    # suma de similitudes contra cada comunidad, gana la mayor
    def most_similar_community(self, policy: Policy) -> str:
        st = self._current()
        best_community = None
        best_score = -1

        for community_id, policy_ids in st.policies_by_community.items():
            community_score = 0.0

            for new_event in policy.events:
                for existing_event in st.events_by_comm.get(community_id, []):
                    score = self._weighted_similarity_score(new_event, existing_event)
                    community_score += score

            if community_score > best_score:
                best_score = community_score
                best_community = community_id

        return best_community

    def _weighted_similarity_score(self, e1: Event, e2: Event) -> float:
        score = 0.0
        weights = self._current().attribute_weights

        def expanded(comp, attr):
            return set(EventMatcher.expand_value(comp.value, attr))

        def overlap(c1, c2, attr):
            s1 = expanded(c1, attr)
            s2 = expanded(c2, attr)
            return bool(s1 & s2) or "*" in s1 or "*" in s2  # comparten valor o alguno es comodin

        if overlap(e1.subject, e2.subject, "rol"):
            score += weights.get("rol", 0.2)
        if overlap(e1.asset, e2.asset, "recurso"):
            score += weights.get("recurso", 0.2)
        if overlap(e1.space, e2.space, "ubicacion"):
            score += weights.get("ubicacion", 0.2)
        if overlap(e1.time, e2.time, "rango_horario"):
            score += weights.get("rango_horario", 0.2)
        if overlap(e1.action, e2.action, "accion"):
            score += weights.get("accion", 0.2)

        return score

    def remove_policy(self, policy_id: str):
        st = self._state
        if policy_id not in st.policies:
            print(f"[WARNING] Política {policy_id} no existe")
            return

        policies = dict(st.policies)
        removed_policy = policies.pop(policy_id, None)
        st = replace(st, policies=policies)

        community_id = st.policy_community.get(policy_id)
        st, released = self._release_exclusions(st, policy_id)
        if community_id is not None:
            st = self._unassign(st, policy_id)
            print(f"[INFO] Política {policy_id} eliminada de comunidad {community_id}")
        else:
            print(f"[WARNING] Política {policy_id} no estaba asignada a ninguna comunidad")
        st = self._reindex_released(st, released, skip=(community_id,))
        self._state = st
        self._notify_policy_change(removed_policy, None, released)

    def update_policy(self, updated_policy: Policy):
        pid = updated_policy.policy_id

        if pid not in self._state.policies:
            print(f"[WARNING] Política {pid} no existe, usar add_policy() en su lugar")
            return

        for e in updated_policy.events:
            if isinstance(e.subject.value, str): e.subject.value = e.subject.value.strip().lower()
            if isinstance(e.asset.value,   str): e.asset.value   = e.asset.value.strip().lower()
            if isinstance(e.space.value,   str): e.space.value   = e.space.value.strip().lower()
            if isinstance(e.time.value,    str): e.time.value    = e.time.value.strip().lower()
            if isinstance(e.action.value,  str): e.action.value  = e.action.value.strip().lower()

        previous_policy = self._state.policies.get(pid)  # se guarda la version anterior para el monitor
        st = replace(self._state, policies={**self._state.policies, pid: updated_policy})

        community_id = st.policy_community.get(pid)
        st, released = self._release_exclusions(st, pid)
        left = None
        if community_id is not None and not (self._in_use(previous_policy) and self._in_use(updated_policy)):
            # se vacio o se desactivo, o venia vacia o inactiva: sin nodos su comunidad ya no dice nada.
            # si vuelve a tener eventos se coloca con la ganancia de modularidad, como una nueva
            st = self._unassign(st, pid)
            left, community_id = community_id, None
        if community_id is None:
            if self._in_use(updated_policy):
                community_id = self._choose_community(st, updated_policy)
                if community_id is not None:
                    st = self._assign(st, pid, community_id)
                print(f"[INFO] Política {pid} reactivada en comunidad {community_id}")
        else:
            st = self._rebuild_community_indices(st, community_id)
            print(f"[INFO] Política {pid} actualizada en comunidad {community_id}")
        st = self._reindex_released(st, released, skip=(left, community_id))
        self._state = st
        self._notify_policy_change(previous_policy, updated_policy, released)

    # desactivar: conserva los eventos, pero salen de los indices, del grafo y de Q (lo mismo que
    # dejarla con events=[]). activar: vuelven y la politica toma comunidad con la ganancia de modularidad
    def deactivate_policy(self, policy_id: str) -> Optional[Policy]:
        return self._set_active(policy_id, False)

    def activate_policy(self, policy_id: str) -> Optional[Policy]:
        return self._set_active(policy_id, True)

    def _set_active(self, policy_id: str, active: bool) -> Optional[Policy]:
        policy = self.policies.get(policy_id)
        if policy is None:
            print(f"[WARNING] Política {policy_id} no existe")
            return None
        if policy.active != active:
            self.update_policy(policy.model_copy(update={"active": active}))  # publica una vez
        return self.policies[policy_id]

    # tiene eventos que se indexan y estan en el grafo
    @staticmethod
    def _in_use(policy: Optional[Policy]) -> bool:
        return policy is not None and policy.active and bool(policy.events)

    def _unassign(self, st: _State, policy_id: str) -> _State:
        community_id = st.policy_community.get(policy_id)
        if community_id is None:
            return st
        st = replace(
            st,
            policies_by_community={
                **st.policies_by_community,
                community_id: [p for p in st.policies_by_community[community_id] if p != policy_id],
            },
            policy_community={p: c for p, c in st.policy_community.items() if p != policy_id},
        )
        return self._rebuild_community_indices(st, community_id)

    # al cambiar o borrar una politica, los eventos que ella cubria vuelven a los indices y los
    # suyos dejan de estar excluidos (su contenido ya no es el que se audito).
    # devuelve el estado y los de otras politicas: event_id -> politica
    def _release_exclusions(self, st: _State, policy_id: str) -> Tuple[_State, Dict[str, str]]:
        touched = {eid: pair for eid, pair in st.excluded.items() if policy_id in pair}
        if touched:
            st = replace(st, excluded={eid: pair for eid, pair in st.excluded.items() if eid not in touched})
        return st, {eid: owner for eid, (owner, _) in touched.items() if owner != policy_id}

    def _reindex_released(self, st: _State, released: Dict[str, str], skip=()) -> _State:
        communities = set()
        for owner in sorted(set(released.values())):
            comm = st.policy_community.get(owner)
            if comm is not None:
                communities.add(comm)
            elif owner in st.policies and st.policies[owner].active:
                # tenia todos sus eventos excluidos y la particion no le dio comunidad
                policy = st.policies[owner]
                usable = policy.model_copy(update={"events": [e for e in policy.events if e.event_id not in st.excluded]})
                comm = self._choose_community(st, usable)
                if comm is not None:
                    st = self._assign(st, owner, comm)  # ya rehace los indices de esa comunidad
        for comm in communities - set(skip):
            st = self._rebuild_community_indices(st, comm)
        return st

    def move_policy_to_community(self, policy_id: str, target_community_id: str):
        st = self._state
        if policy_id not in st.policies:
            print(f"[WARNING] Política {policy_id} no existe")
            return

        source_community = st.policy_community.get(policy_id)
        policies_by_community = dict(st.policies_by_community)
        if source_community is not None:
            policies_by_community[source_community] = [
                p for p in policies_by_community[source_community] if p != policy_id
            ]
        policies_by_community[target_community_id] = policies_by_community.get(target_community_id, []) + [policy_id]
        st = replace(
            st,
            policies_by_community=policies_by_community,
            policy_community={**st.policy_community, policy_id: target_community_id},
        )

        if source_community is not None:
            st = self._rebuild_community_indices(st, source_community)
        st = self._rebuild_community_indices(st, target_community_id)
        self._state = st

        print(f"[INFO] Política {policy_id} movida de {source_community} a {target_community_id}")
        policy = st.policies[policy_id]
        self._notify_policy_change(policy, policy)

    # comunidades donde podria haber un match
    def _candidate_communities(
        self,
        request: AccessRequest,
        value_index: Dict[str, Dict[str, Set[str]]] = None,
        tree_index: IntervalTree = None,
    ) -> Set[str]:
        # por defecto los indices generales, en evaluate tambien se usa con los de deny
        if value_index is None:
            value_index = self.value_to_comms
        if tree_index is None:
            tree_index = self.interval_tree

        time_val = request.time.value.strip().lower()

        vals = {
            "rol":      request.subject.value.strip().lower(),
            "recurso":  request.asset.value.strip().lower(),
            "ubicacion":request.space.value.strip().lower(),
            "accion":   request.action.value.strip().lower(),
        }

        candidate_sets = []

        for attr, val in vals.items():
            attr_index = value_index.get(attr, {})
            exact    = attr_index.get(val, set())
            wildcard = attr_index.get("*", set())
            combined = exact | wildcard  # valor exacto o comodin
            # ningun evento acepta este valor
            if not combined:
                return set()
            candidate_sets.append(combined)

        rh_index   = value_index.get("rango_horario", {})
        rh_exact   = rh_index.get(time_val, set())
        rh_wildcard= rh_index.get("*", set())

        if _is_time_format(time_val):
            rh_range = tree_index.query(time_val)  # rangos horarios que contienen la hora
            rh_combined = rh_exact | rh_wildcard | rh_range
        else:
            rh_combined = rh_exact | rh_wildcard

        if not rh_combined:
            return set()
        candidate_sets.append(rh_combined)

        return set.intersection(*candidate_sets)  # tienen que cumplir los 5 atributos

    # que tan comunes son los valores de la peticion en la comunidad
    def _score_comm(self, comm: str, req_vals: Dict[str, str], freq=None) -> float:
        if freq is None:
            freq = self.freq
        s = 0.0
        for attr in self.ATTRS:
            s += freq.get(comm, {}).get(attr, {}).get(req_vals[attr], 0.0)
        return s

    def evaluate(self, request: AccessRequest) -> dict:
        start = time.time()
        st = self._state  # una sola lectura: todo lo de abajo usa esta instancia

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

        candidates = self._candidate_communities(request, st.value_to_comms, st.interval_tree)

        if not candidates:
            eval_time = time.time() - start
            # sin candidatos: deny directo
            return {"result": "deny", "policy_id": None, "event_id": None, "eval_time": eval_time, "steps": 0}

        steps = 0
        policies, policies_by_community, excluded = st.policies, st.policies_by_community, st.excluded

        # primero se buscan los deny, para que deny siempre gane
        deny_candidates = self._candidate_communities(request, st.value_to_comms_deny, st.interval_tree_deny)

        if deny_candidates:
            sorted_deny = sorted(
                deny_candidates,
                key=lambda c: self._score_comm(c, req_vals, st.freq),
                reverse=True,
            )
            for comm in sorted_deny:
                for pid in policies_by_community.get(comm, []):
                    policy = policies.get(pid)
                    # en esta pasada solo cuentan los deny
                    if not policy or not policy.active or policy.effect != "deny":
                        continue
                    for event in policy.events:
                        if event.event_id in excluded:
                            continue
                        steps += 1  # eventos comparados
                        if EventMatcher.match_event(event, request):
                            eval_time = time.time() - start
                            return {
                                "result": "deny",
                                "policy_id": policy.policy_id,
                                "event_id": event.event_id,
                                "eval_time": eval_time,
                                "steps": steps,
                            }

        # luego el recorrido normal, primer match gana
        sorted_candidates = sorted(
            candidates,
            key=lambda c: self._score_comm(c, req_vals, st.freq),
            reverse=True,
        )

        for comm in sorted_candidates:
            already_checked_deny = comm in deny_candidates
            for pid in policies_by_community.get(comm, []):
                policy = policies.get(pid)
                if not policy or not policy.active:
                    continue
                # ya se revisaron arriba
                if already_checked_deny and policy.effect == "deny":
                    continue
                for event in policy.events:
                    if event.event_id in excluded:
                        continue
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

        eval_time = time.time() - start
        # nada aplica: deny por defecto
        return {"result": "deny", "policy_id": None, "event_id": None, "eval_time": eval_time, "steps": steps}

    def get_statistics(self) -> dict:
        st = self._state
        stats = {
            "total_policies": len(st.policies),
            "total_communities": len(st.policies_by_community),
            "communities": {}
        }

        for comm_id, policy_ids in st.policies_by_community.items():
            stats["communities"][comm_id] = {
                "num_policies": len(policy_ids),
                "num_events": len(st.events_by_comm.get(comm_id, [])),
                "top_values": {
                    attr: sorted(
                        st.freq.get(comm_id, {}).get(attr, {}).items(),
                        key=lambda x: x[1],
                        reverse=True
                    )[:5]
                    for attr in self.ATTRS
                }
            }

        return stats

    def validate_integrity(self) -> List[str]:
        st = self._state
        issues = []

        for comm_id, policy_ids in st.policies_by_community.items():
            for pid in policy_ids:
                if pid not in st.policies:
                    issues.append(f"Política {pid} en comunidad {comm_id} no existe en self.policies")

        assigned_pids = set()
        for policy_ids in st.policies_by_community.values():
            assigned_pids.update(policy_ids)

        for pid in st.policies:
            if pid not in assigned_pids:
                # sin eventos que usar (inactiva, vacia o con todos excluidos): es normal que no tenga comunidad
                policy = st.policies[pid]
                if not policy.active or all(e.event_id in st.excluded for e in policy.events):
                    continue
                issues.append(f"Política {pid} no está asignada a ninguna comunidad")

        for comm_id, events in st.events_by_comm.items():
            expected_count = sum(
                1
                for pid in st.policies_by_community.get(comm_id, [])
                if pid in st.policies and st.policies[pid].active
                for e in st.policies[pid].events
                if e.event_id not in st.excluded
            )
            if len(events) != expected_count:
                issues.append(f"Comunidad {comm_id}: eventos={len(events)}, esperados={expected_count}")

        return issues
