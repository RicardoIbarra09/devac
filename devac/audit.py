"""
devac/audit.py — Detección y mitigación de redundancia y conflictos entre políticas.

Aprovecha la partición por comunidades (ya calculada por CommunityDetector) para
evitar comparar cada evento contra todos los demás del sistema: dentro de cada
comunidad se comparan todos los pares (O(k^2) por comunidad, sumado O(n^2/C) en
total en vez de O(n^2) global), y los eventos con comodín ("*") en algún atributo
se cruzan además contra el resto de comunidades, porque un comodín puede cubrir
eventos semánticamente lejanos que la similitud de GraphBuilder no conectaría.

Redundancia: evento A cubre (⊇) o es igual a evento B, y ambos declaran el mismo
efecto -> B es redundante, se puede retirar sin cambiar ninguna decisión.

Conflicto: evento A y evento B se traslapan (comparten al menos una solicitud
posible, incluida la contención total) pero declaran efectos distintos.

Mitigación:
  - Redundancia: se retira el evento redundante del conjunto de trabajo (no se
    modifica el esquema de Policy/Event; solo se excluye de lo que se le pasa a
    evaluator.run()). Queda registrado en el reporte para poder revertirlo si el
    panorama cambia después.
  - Conflicto: se reordena, dentro de su comunidad, la lista de policy_id para
    que la política con el efecto "ganador" (deny por defecto) se evalúe antes
    que la perdedora — coincide exactamente con la semántica real de
    "primer match gana" que ya usa evaluate(), sin inventar particiones
    geométricas de cobertura que el modelo de datos no soporta.
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Tuple

from devac.graph import GraphBuilder
from devac.interval_tree import _to_minutes
from devac.models import Event, Policy

ATTRS = ("subject", "asset", "space", "time", "action")

# Los pesos de entropía que ya calcula GraphBuilder usan las claves en
# español de graph.py/evaluator.py; este mapa traduce a los nombres de
# campo reales de Event (ver CLAUDE.md: son dos vocabularios distintos
# para las mismas cinco dimensiones).
_ATTR_TO_WEIGHT_KEY = {
    "subject": "rol", "asset": "recurso", "space": "ubicacion",
    "time": "rango_horario", "action": "accion",
}


class EventRelation(Enum):
    EQUAL = "equal"
    SUPERSET = "superset"   # e1 cubre a e2
    SUBSET = "subset"       # e2 cubre a e1
    OVERLAP = "overlap"
    DISJOINT = "disjoint"


def _range_segments(lo_min, hi_min):
    """Normaliza un rango horario (en minutos, 0..1439) a una lista de
    segmentos bien formados (inicio <= fin). Si lo_min > hi_min, el rango
    cruza medianoche (p. ej. 23:00-06:00) y se representa como dos
    segmentos: [lo_min, 1439] y [0, hi_min] -- la misma particion que ya
    usa IntervalTree.insert() para indexarlo."""
    if lo_min is None or hi_min is None:
        return []
    if lo_min <= hi_min:
        return [(lo_min, hi_min)]
    return [(lo_min, 1439), (0, hi_min)]


def _as_set_spec(value):
    """Normaliza un valor del DSL a una forma comparable:
    ("wild",) | ("exact", v) | ("set", frozenset) | ("range", [(lo,hi), ...])."""
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


def _covers(a: tuple, b: tuple) -> bool:
    """¿El conjunto representado por 'a' contiene por completo al de 'b'?"""
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
        return False  # un conjunto discreto no puede probarse que cubre un range continuo
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
            # A cubre B si CADA segmento de B queda contenido por completo
            # dentro de ALGUN segmento de A (no basta con la union a trozos,
            # porque un segmento de B podria quedar repartido entre dos
            # segmentos de A sin que ninguno solo lo cubra entero).
            return bool(segs_b) and all(
                any(alo <= blo and bhi <= ahi for alo, ahi in segs_a)
                for blo, bhi in segs_b
            )
    return False


def _intersects(a: tuple, b: tuple) -> bool:
    """¿Comparten a y b al menos un valor posible?"""
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
    return False  # exact vs exact / exact vs set / exact vs range: ya resuelto por _covers


def compare_events(e1: Event, e2: Event) -> EventRelation:
    """Compara dos eventos atributo por atributo (conjunción de las 5 dimensiones)
    y determina su relación de cobertura/traslape global."""
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


def _has_significant_wildcard(event: Event, weights: Dict[str, float], epsilon: float) -> bool:
    """
    ¿Tiene el evento un comodín en algún atributo que sí pesa lo suficiente
    como para arriesgar perder una relación entre comunidades distintas?

    Un atributo con peso ~0 (casi todos los eventos usan "*" ahí, como suele
    pasar con rango_horario) casi no influyó en cómo Louvain separó las
    comunidades -- si dos eventos terminaron en comunidades distintas, es
    por los atributos que sí pesan, y esos ya bastan para que sean disjuntos.
    Cruzarlos igual solo por un comodín de bajo peso desperdicia trabajo sin
    encontrar nada nuevo.
    """
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

    def summary(self) -> str:
        return (
            f"{len(self.redundant_events)} eventos redundantes retirados, "
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
    """
    Detecta y mitiga redundancia y conflictos entre políticas, aprovechando la
    partición por comunidades para evitar comparar todo contra todo.

    wildcard_weight_epsilon: un comodín en un atributo con peso de entropía
    por debajo de este umbral no dispara el cruce entre comunidades (ver
    _has_significant_wildcard) -- evita degenerar a O(n^2) cuando, como es
    convención en este proyecto, la mayoría de las políticas dejan
    rango_horario="*".

    No modifica las políticas de entrada; trabaja sobre copias. Devuelve
    (políticas limpias, comunidades posiblemente reordenadas, reporte).
    """
    start = time.perf_counter()
    report = PolicyAuditReport()

    policies_by_id: Dict[str, Policy] = {p.policy_id: copy.deepcopy(p) for p in policies}
    weights = GraphBuilder.calculate_attribute_weights(policies)

    events_by_comm: Dict[str, List[Tuple[str, Event]]] = {}
    wildcard_events: List[Tuple[str, str, Event]] = []  # (comunidad, policy_id, event)

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

    redundant_marks: set = set()  # (policy_id, event_id, dominante_pid, dominante_eid)
    processed_pairs: set = set()  # evita comparar/reportar el mismo par dos veces

    def process_pair(pid1: str, e1: Event, pid2: str, e2: Event) -> None:
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

        if effect1 != effect2:
            report.conflicts.append(PolicyConflict(
                policy_a=pid1, event_a=e1.event_id, effect_a=effect1,
                policy_b=pid2, event_b=e2.event_id, effect_b=effect2,
                relation=rel.value,
            ))
            return

        if rel == EventRelation.SUPERSET:
            redundant_marks.add((pid2, e2.event_id, pid1, e1.event_id))
        elif rel == EventRelation.SUBSET:
            redundant_marks.add((pid1, e1.event_id, pid2, e2.event_id))
        elif rel == EventRelation.EQUAL:
            # Duplicado exacto: ninguna cobertura manda sobre la otra, así
            # que el desempate es por otro criterio. Primero se prefiere
            # conservar la política con reconfigure_on no vacío -- perderla
            # silenciosamente rompería su capacidad de reaccionar a eventos.
            # Solo si ambas (o ninguna) lo tienen, se desempata por
            # policy_id (determinístico, no depende de orden de iteración).
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

    # --- Paso 1: dentro de cada comunidad, todos los pares ---
    for bucket in events_by_comm.values():
        for i in range(len(bucket)):
            pid1, e1 = bucket[i]
            for j in range(i + 1, len(bucket)):
                pid2, e2 = bucket[j]
                process_pair(pid1, e1, pid2, e2)

    # --- Paso 2: eventos con comodín, cruzados contra el resto de comunidades ---
    for comm_w, pid_w, e_w in wildcard_events:
        for comm2, bucket in events_by_comm.items():
            if comm2 == comm_w:
                continue
            for pid2, e2 in bucket:
                process_pair(pid_w, e_w, pid2, e2)

    # --- Mitigación: redundancia -> retirar eventos dominados ---
    to_remove_by_policy: Dict[str, set] = {}
    for pid, eid, dom_pid, dom_eid in redundant_marks:
        report.redundant_events.append(RedundantEvent(
            policy_id=pid, event_id=eid,
            dominated_by_policy=dom_pid, dominated_by_event=dom_eid,
        ))
        to_remove_by_policy.setdefault(pid, set()).add(eid)
    for pid, eids in to_remove_by_policy.items():
        policy = policies_by_id[pid]
        policy.events = [e for e in policy.events if e.event_id not in eids]

    # --- Mitigación: conflicto -> reordenar para que gane conflict_priority ---
    policy_to_comm = {pid: comm for comm, pids in policies_by_community.items() for pid in pids}
    losers_by_comm: Dict[str, set] = {}
    for c in report.conflicts:
        for pid, effect in ((c.policy_a, c.effect_a), (c.policy_b, c.effect_b)):
            if effect != conflict_priority:
                comm = policy_to_comm.get(pid)
                if comm is not None:
                    losers_by_comm.setdefault(comm, set()).add(pid)

    new_policies_by_community = {c: list(pids) for c, pids in policies_by_community.items()}
    for comm, losers in losers_by_comm.items():
        pids = new_policies_by_community[comm]
        new_order = sorted(pids, key=lambda p: 1 if p in losers else 0)
        if new_order != pids:
            new_policies_by_community[comm] = new_order
            report.reordered_communities.append(comm)

    report.duration_seconds = time.perf_counter() - start
    return list(policies_by_id.values()), new_policies_by_community, report
