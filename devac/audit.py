from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Tuple

from devac.graph import GraphBuilder
from devac.interval_tree import _to_minutes
from devac.models import Event, Policy

ATTRS = ("subject", "asset", "space", "time", "action")

# nombre del campo -> clave del peso de entropia
_ATTR_TO_WEIGHT_KEY = {
    "subject": "rol", "asset": "recurso", "space": "ubicacion",
    "time": "rango_horario", "action": "accion",
}


class EventRelation(Enum):
    EQUAL = "equal"
    SUPERSET = "superset"  # e1 cubre a e2
    SUBSET = "subset"  # e2 cubre a e1
    OVERLAP = "overlap"
    DISJOINT = "disjoint"


# un rango que cruza medianoche se parte en dos
def _range_segments(lo_min, hi_min):
    if lo_min is None or hi_min is None:
        return []
    if lo_min <= hi_min:
        return [(lo_min, hi_min)]
    return [(lo_min, 1439), (0, hi_min)]


# convierte el valor a algo comparable: wild, exact, set o range
def _as_set_spec(value):
    if isinstance(value, str):
        v = value.strip().lower()
        if v == "*":
            return ("wild",)
        return ("exact", v)
    if isinstance(value, dict):
        if "in" in value:
            return ("set", frozenset(str(v).strip().lower() for v in value["in"]))
        if "range" in value:
            lo, hi = value["range"]
            return ("range", _range_segments(_to_minutes(str(lo)), _to_minutes(str(hi))))
    return ("exact", str(value).strip().lower())


# a contiene todo lo que acepta b?
def _covers(a: tuple, b: tuple) -> bool:
    ta, tb = a[0], b[0]
    if ta == "wild":
        return True
    if tb == "wild":
        return False
    if ta == "exact":
        return tb == "exact" and a[1] == b[1]
    if ta == "set":
        if tb == "exact":
            return b[1] in a[1]
        if tb == "set":
            return b[1].issubset(a[1])
        return False
    if ta == "range":
        segs_a = a[1]
        if not segs_a:
            return False
        if tb == "exact":
            m = _to_minutes(b[1])
            return m is not None and any(lo <= m <= hi for lo, hi in segs_a)
        if tb == "set":
            ms = [_to_minutes(v) for v in b[1]]
            return len(ms) > 0 and all(
                m is not None and any(lo <= m <= hi for lo, hi in segs_a) for m in ms
            )
        if tb == "range":
            segs_b = b[1]
            return bool(segs_b) and all(
                # cada pedazo de b cabe completo en un pedazo de a
                any(alo <= blo and bhi <= ahi for alo, ahi in segs_a)
                for blo, bhi in segs_b
            )
    return False


# a y b comparten al menos un valor?
def _intersects(a: tuple, b: tuple) -> bool:
    if _covers(a, b) or _covers(b, a):
        return True
    ta, tb = a[0], b[0]
    if ta == "set" and tb == "set":
        return len(a[1] & b[1]) > 0
    if ta == "range" and tb == "range":
        segs_a, segs_b = a[1], b[1]
        if not segs_a or not segs_b:
            return False
        return any(alo <= bhi and blo <= ahi for alo, ahi in segs_a for blo, bhi in segs_b)
    if "range" in (ta, tb) and "set" in (ta, tb):
        rng, st = (a, b) if ta == "range" else (b, a)
        segs = rng[1]
        if not segs:
            return False
        for v in st[1]:
            m = _to_minutes(v)
            if m is not None and any(lo <= m <= hi for lo, hi in segs):
                return True
        return False
    return False


# relacion entre dos eventos mirando las 5 dimensiones
def compare_events(e1: Event, e2: Event) -> EventRelation:
    specs1 = {a: _as_set_spec(getattr(e1, a).value) for a in ATTRS}
    specs2 = {a: _as_set_spec(getattr(e2, a).value) for a in ATTRS}

    covers_1_2 = all(_covers(specs1[a], specs2[a]) for a in ATTRS)
    covers_2_1 = all(_covers(specs2[a], specs1[a]) for a in ATTRS)
    if covers_1_2 and covers_2_1:
        return EventRelation.EQUAL
    if covers_1_2:
        return EventRelation.SUPERSET
    if covers_2_1:
        return EventRelation.SUBSET

    if all(_intersects(specs1[a], specs2[a]) for a in ATTRS):
        return EventRelation.OVERLAP
    return EventRelation.DISJOINT


# comodin en un atributo que si pesa
def _has_significant_wildcard(event: Event, weights: Dict[str, float], epsilon: float) -> bool:
    for attr in ATTRS:
        if getattr(event, attr).value == "*":
            weight_key = _ATTR_TO_WEIGHT_KEY[attr]
            if weights.get(weight_key, 0.0) > epsilon:
                return True
    return False


@dataclass
class RedundantEvent:
    policy_id: str
    event_id: str
    dominated_by_policy: str
    dominated_by_event: str


@dataclass
class PolicyConflict:
    policy_a: str
    event_a: str
    effect_a: str
    policy_b: str
    event_b: str
    effect_b: str
    relation: str


@dataclass
class PolicyAuditReport:
    redundant_events: List[RedundantEvent] = field(default_factory=list)
    conflicts: List[PolicyConflict] = field(default_factory=list)
    reordered_communities: List[str] = field(default_factory=list)
    events_compared: int = 0
    duration_seconds: float = 0.0

    # event_id -> (politica del evento, politica que lo cubre); el evaluador los deja fuera de sus indices.
    # si lo cubren varias basta con una: la relacion no tiene ciclos, asi que la cadena termina en un
    # evento que si esta en los indices
    def exclusions(self) -> Dict[str, Tuple[str, str]]:
        found: Dict[str, Tuple[str, str]] = {}
        for r in self.redundant_events:
            found.setdefault(r.event_id, (r.policy_id, r.dominated_by_policy))
        return found

    def summary(self) -> str:
        return (
            f"{len(self.redundant_events)} eventos redundantes excluidos de los índices, "
            f"{len(self.conflicts)} conflictos resueltos, "
            f"{len(self.reordered_communities)} comunidades reordenadas "
            f"({self.events_compared} comparaciones, {self.duration_seconds * 1000:.2f} ms)"
        )


def audit_and_mitigate(
    policies: List[Policy],
    policies_by_community: Dict[str, List[str]],
    conflict_priority: str = "deny",
    wildcard_weight_epsilon: float = 0.02,
) -> Tuple[List[Policy], Dict[str, List[str]], PolicyAuditReport]:
    start = time.perf_counter()
    report = PolicyAuditReport()

    # las politicas no se modifican: un evento redundante sigue en su politica y solo se reporta,
    # porque deja de serlo si cambia o se borra la politica que lo cubre
    policies_by_id: Dict[str, Policy] = {p.policy_id: p for p in policies}
    weights = GraphBuilder.calculate_attribute_weights(policies)

    events_by_comm: Dict[str, List[Tuple[str, Event]]] = {}
    # estos se cruzan tambien contra otras comunidades
    wildcard_events: List[Tuple[str, str, Event]] = []

    for comm, pids in policies_by_community.items():
        bucket: List[Tuple[str, Event]] = []
        for pid in pids:
            policy = policies_by_id.get(pid)
            if not policy:
                continue
            for event in policy.events:
                bucket.append((pid, event))
                if _has_significant_wildcard(event, weights, wildcard_weight_epsilon):
                    wildcard_events.append((comm, pid, event))
        events_by_comm[comm] = bucket

    redundant_marks: set = set()
    processed_pairs: set = set()

    def process_pair(pid1: str, e1: Event, pid2: str, e2: Event) -> None:
        # mismo par sin importar el orden
        key = tuple(sorted(((pid1, e1.event_id), (pid2, e2.event_id))))
        if key[0] == key[1] or key in processed_pairs:
            return
        processed_pairs.add(key)
        report.events_compared += 1
        rel = compare_events(e1, e2)
        if rel == EventRelation.DISJOINT:
            return

        effect1 = policies_by_id[pid1].effect
        effect2 = policies_by_id[pid2].effect

        # se traslapan con efectos distintos: conflicto
        if effect1 != effect2:
            report.conflicts.append(PolicyConflict(
                policy_a=pid1, event_a=e1.event_id, effect_a=effect1,
                policy_b=pid2, event_b=e2.event_id, effect_b=effect2,
                relation=rel.value,
            ))
            return

        # mismo efecto: el cubierto sobra
        if rel == EventRelation.SUPERSET:
            redundant_marks.add((pid2, e2.event_id, pid1, e1.event_id))
        elif rel == EventRelation.SUBSET:
            redundant_marks.add((pid1, e1.event_id, pid2, e2.event_id))
        # duplicados: se queda el que tiene reconfigure_on, si no el de id menor
        elif rel == EventRelation.EQUAL:
            reconf1 = bool(policies_by_id[pid1].reconfigure_on)
            reconf2 = bool(policies_by_id[pid2].reconfigure_on)
            if reconf1 and not reconf2:
                keep_pid, keep_e, drop_pid, drop_e = pid1, e1, pid2, e2
            elif reconf2 and not reconf1:
                keep_pid, keep_e, drop_pid, drop_e = pid2, e2, pid1, e1
            elif pid1 <= pid2:
                keep_pid, keep_e, drop_pid, drop_e = pid1, e1, pid2, e2
            else:
                keep_pid, keep_e, drop_pid, drop_e = pid2, e2, pid1, e1
            redundant_marks.add((drop_pid, drop_e.event_id, keep_pid, keep_e.event_id))

    # todos contra todos dentro de cada comunidad
    for bucket in events_by_comm.values():
        for i in range(len(bucket)):
            pid1, e1 = bucket[i]
            for j in range(i + 1, len(bucket)):
                pid2, e2 = bucket[j]
                process_pair(pid1, e1, pid2, e2)

    # comodines contra el resto de comunidades
    for comm_w, pid_w, e_w in wildcard_events:
        for comm2, bucket in events_by_comm.items():
            if comm2 == comm_w:
                continue
            for pid2, e2 in bucket:
                process_pair(pid_w, e_w, pid2, e2)

    for pid, eid, dom_pid, dom_eid in sorted(redundant_marks):
        report.redundant_events.append(RedundantEvent(
            policy_id=pid, event_id=eid,
            dominated_by_policy=dom_pid, dominated_by_event=dom_eid,
        ))

    new_policies_by_community, report.reordered_communities = order_by_priority(
        policies_by_community, report.conflicts, conflict_priority
    )

    report.duration_seconds = time.perf_counter() - start
    return list(policies_by_id.values()), new_policies_by_community, report


# en cada comunidad, las politicas con el efecto ganador de un conflicto van primero.
# devuelve la particion reordenada y las comunidades que cambiaron
def order_by_priority(
    policies_by_community: Dict[str, List[str]],
    conflicts: List[PolicyConflict],
    conflict_priority: str = "deny",
) -> Tuple[Dict[str, List[str]], List[str]]:
    policy_to_comm = {pid: comm for comm, pids in policies_by_community.items() for pid in pids}
    losers_by_comm: Dict[str, set] = {}
    for c in conflicts:
        for pid, effect in ((c.policy_a, c.effect_a), (c.policy_b, c.effect_b)):
            if effect != conflict_priority:
                comm = policy_to_comm.get(pid)
                if comm is not None:
                    losers_by_comm.setdefault(comm, set()).add(pid)

    ordered = {c: list(pids) for c, pids in policies_by_community.items()}
    reordered = []
    for comm, losers in losers_by_comm.items():
        pids = ordered[comm]
        new_order = sorted(pids, key=lambda p: 1 if p in losers else 0)
        if new_order != pids:
            ordered[comm] = new_order
            reordered.append(comm)
    return ordered, reordered
