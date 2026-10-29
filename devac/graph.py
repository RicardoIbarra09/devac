import networkx as nx
from devac.models import Event, Policy
from typing import Dict, Iterable, List, Optional, Set, Tuple
from collections import Counter, defaultdict
import numpy as np
import time
from pathlib import Path

# la cubeta de tiempo se llama distinto que su peso
_PREFIX_TO_WEIGHT_KEY = {
    "rol": "rol", "recurso": "recurso", "ubicacion": "ubicacion",
    "tiempo": "rango_horario", "accion": "accion",
}


class GraphBuilder:
    def __init__(self):
        self.graph = None
        self.last_stats: dict = {}  # tiempos y tamanos del ultimo build

        # se conserva entre builds para los cambios en caliente (add_events / remove_events).
        # los pesos quedan congelados hasta el siguiente build: el grafo incremental es exacto
        # para estos pesos, pero se va separando del que daria un build con los pesos de las
        # politicas vigentes. ese es el segundo origen de desgaste, junto con la particion
        # que no se recalcula; los dos se corrigen en la reconstruccion
        self.weights: Dict[str, float] = {}
        self.similarity_threshold: float = 0.3
        self.omitibles: Set[str] = set()
        self._index: Dict[str, Dict[str, None]] = {}  # valor -> eventos (dict como conjunto ordenado)
        self._nodes: Dict[str, Tuple[Event, Tuple[str, ...]]] = {}  # event_id -> evento y sus cubetas

    def event_similarity(self, e1: Event, e2: Event, weights: dict) -> float:
        def _eq(a, b):
            if isinstance(a, dict) or isinstance(b, dict):
                return False
            return a == b

        score = 0.0
        # suma el peso de cada atributo que comparten
        if _eq(e1.subject.value, e2.subject.value): score += weights.get("rol", 0)
        if _eq(e1.asset.value,   e2.asset.value):   score += weights.get("recurso", 0)
        if _eq(e1.space.value,   e2.space.value):   score += weights.get("ubicacion", 0)
        if _eq(e1.time.value,    e2.time.value):    score += weights.get("rango_horario", 0)
        if _eq(e1.action.value,  e2.action.value):  score += weights.get("accion", 0)
        return score

    @staticmethod
    def _normalize(event: Event) -> None:
        if isinstance(event.subject.value, str): event.subject.value = event.subject.value.strip().lower()
        if isinstance(event.asset.value,   str): event.asset.value   = event.asset.value.strip().lower()
        if isinstance(event.space.value,   str): event.space.value   = event.space.value.strip().lower()
        if isinstance(event.time.value,    str): event.time.value    = event.time.value.strip().lower()
        if isinstance(event.action.value,  str): event.action.value  = event.action.value.strip().lower()

    @staticmethod
    def _node_attrs(event: Event, policy_id: str) -> dict:
        def _gephi(v):
            return str(v) if isinstance(v, dict) else v

        return dict(
            policy_id     = policy_id,
            rol           = _gephi(event.subject.value),
            recurso       = _gephi(event.asset.value),
            ubicacion     = _gephi(event.space.value),
            rango_horario = _gephi(event.time.value),
            accion        = _gephi(event.action.value),
        )

    # atributos que aunque coincidan nunca alcanzan el umbral
    @staticmethod
    def _omitibles_for(weights: Dict[str, float], similarity_threshold: float) -> Set[str]:
        omitibles = set()
        suma = 0.0
        for attr, peso in sorted(weights.items(), key=lambda kv: kv[1]):
            if suma + peso < similarity_threshold:
                suma += peso
                omitibles.add(attr)
        return omitibles

    @staticmethod
    def _index_keys(event: Event, omitibles: Set[str]) -> Tuple[str, ...]:
        keys = []
        for prefix, val in (("rol", event.subject.value), ("recurso", event.asset.value),
                            ("ubicacion", event.space.value), ("tiempo", event.time.value),
                            ("accion", event.action.value)):
            # no vale la pena indexarlo
            if _PREFIX_TO_WEIGHT_KEY[prefix] in omitibles:
                continue
            if isinstance(val, str):
                keys.append(f"{prefix}:{val}")
        return tuple(keys)

    # weights: pesos ya calculados (p. ej. los congelados); si no, se calculan por entropia
    def build_event_graph(self, policies: List[Policy], similarity_threshold: float = 0.3,
                          export: bool = True, weights: Optional[Dict[str, float]] = None) -> nx.Graph:
        G = nx.Graph()
        t0 = time.perf_counter()
        attribute_weights = dict(weights) if weights is not None else self.calculate_attribute_weights(policies)
        t_weights = time.perf_counter() - t0

        t0 = time.perf_counter()
        all_events_map = {}  # event_id -> evento y su politica

        for policy in policies:
            if not policy.active:
                continue  # sus eventos no entran al grafo hasta que se active
            for event in policy.events:
                self._normalize(event)
                all_events_map[event.event_id] = {"obj": event, "policy_id": policy.policy_id}
                G.add_node(event.event_id, **self._node_attrs(event, policy.policy_id))  # un nodo por evento
        t_nodes = time.perf_counter() - t0

        t0 = time.perf_counter()
        omitibles = self._omitibles_for(attribute_weights, similarity_threshold)

        inverted_index: Dict[str, Dict[str, None]] = defaultdict(dict)  # valor -> eventos que lo usan
        event_keys: Dict[str, Tuple[str, ...]] = {}
        for ev_id, data in all_events_map.items():
            keys = self._index_keys(data["obj"], omitibles)
            event_keys[ev_id] = keys
            for key in keys:
                inverted_index[key][ev_id] = None
        t_index = time.perf_counter() - t0

        t0 = time.perf_counter()
        processed_pairs: Set[Tuple[str, str]] = set()  # para no comparar dos veces el mismo par

        # solo se comparan eventos de la misma cubeta
        for key, bucket in inverted_index.items():
            if len(bucket) < 2:
                continue
            event_ids_in_bucket = list(bucket)
            for i in range(len(event_ids_in_bucket)):
                for j in range(i + 1, len(event_ids_in_bucket)):
                    id_a = event_ids_in_bucket[i]
                    id_b = event_ids_in_bucket[j]
                    pair = tuple(sorted((id_a, id_b)))
                    if pair in processed_pairs:
                        continue
                    processed_pairs.add(pair)
                    e1 = all_events_map[id_a]["obj"]
                    e2 = all_events_map[id_b]["obj"]
                    sim = self.event_similarity(e1, e2, attribute_weights)
                    # arista si son suficientemente parecidos
                    if sim >= similarity_threshold:
                        G.add_edge(id_a, id_b, weight=float(sim))
        t_pairs = time.perf_counter() - t0

        n = G.number_of_nodes()
        bucket_sizes = [len(ids) for ids in inverted_index.values() if len(ids) >= 2]
        self.last_stats = {
            "t_weights_s": t_weights,
            "t_nodes_s": t_nodes,
            "t_index_s": t_index,
            "t_pairs_s": t_pairs,
            "t_total_s": t_weights + t_nodes + t_index + t_pairs,  # sin la exportacion
            "nodes": n,
            "edges": G.number_of_edges(),
            "pairs_compared": len(processed_pairs),
            "pairs_total": n * (n - 1) // 2,
            "buckets": len(bucket_sizes),
            "max_bucket": max(bucket_sizes, default=0),
            "omitibles": sorted(omitibles),
        }

        self.graph = G
        self.weights = attribute_weights
        self.similarity_threshold = similarity_threshold
        self.omitibles = omitibles
        self._index = dict(inverted_index)
        self._nodes = {ev_id: (data["obj"], event_keys[ev_id]) for ev_id, data in all_events_map.items()}
        if export:
            self.export_graph("exports/event_graph.gexf")  # para abrirlo en gephi
        return self.graph

    # eventos ya en el grafo que pasarian el umbral con este; no modifica nada
    def similar_events(self, event: Event) -> Dict[str, float]:
        self._normalize(event)
        found: Dict[str, float] = {}
        seen = {event.event_id}
        for key in self._index_keys(event, self.omitibles):
            for other_id in self._index.get(key, ()):
                if other_id in seen:
                    continue
                seen.add(other_id)  # mismo par en varias cubetas: se compara una vez
                sim = self.event_similarity(event, self._nodes[other_id][0], self.weights)
                if sim >= self.similarity_threshold:
                    found[other_id] = float(sim)
        return found

    # mismos nodos y aristas que daria build_event_graph con los pesos congelados.
    # events: solo esos eventos de la politica (por defecto todos)
    def add_events(self, policy: Policy, events: Optional[Iterable[Event]] = None) -> List[str]:
        if self.graph is None:
            self.graph = nx.Graph()
        added = []
        for event in policy.events if events is None else events:
            if event.event_id in self._nodes:
                raise ValueError(f"el evento {event.event_id} ya esta en el grafo")
            neighbors = self.similar_events(event)
            keys = self._index_keys(event, self.omitibles)
            self.graph.add_node(event.event_id, **self._node_attrs(event, policy.policy_id))
            for other_id, sim in neighbors.items():
                self.graph.add_edge(event.event_id, other_id, weight=sim)
            for key in keys:
                self._index.setdefault(key, {})[event.event_id] = None
            self._nodes[event.event_id] = (event, keys)
            added.append(event.event_id)
        return added

    def remove_events(self, event_ids: Iterable[str]) -> List[str]:
        removed = []
        for event_id in event_ids:
            entry = self._nodes.pop(event_id, None)
            if entry is None:
                continue
            # se usan las cubetas guardadas, por si el evento cambio despues de insertarse
            for key in entry[1]:
                bucket = self._index[key]
                bucket.pop(event_id, None)
                if not bucket:
                    del self._index[key]
            self.graph.remove_node(event_id)
            removed.append(event_id)
        return removed

    def __contains__(self, event_id: str) -> bool:
        return event_id in self._nodes

    @staticmethod
    def calculate_attribute_weights(policies: List[Policy]) -> dict:
        attribute_values = {
            "rol": [], "recurso": [], "ubicacion": [], "rango_horario": [], "accion": []
        }

        def _str(v):
            # in/range cuentan como un solo valor
            return v.strip().lower() if isinstance(v, str) else "__complex__"

        for policy in policies:
            if not policy.active:
                continue
            for event in policy.events:
                attribute_values["rol"].append(_str(event.subject.value))
                attribute_values["recurso"].append(_str(event.asset.value))
                attribute_values["ubicacion"].append(_str(event.space.value))
                attribute_values["rango_horario"].append(_str(event.time.value))
                attribute_values["accion"].append(_str(event.action.value))

        entropies = {}
        for attribute, values in attribute_values.items():
            if not values:
                entropies[attribute] = 0
                continue
            counts = Counter(values)
            total = len(values)
            probabilities = [freq / total for freq in counts.values()]
            # entropia de shannon
            entropies[attribute] = -sum(p * np.log2(p) for p in probabilities if p > 0)

        total_entropy = sum(entropies.values()) or 1.0
        # normalizado para que sumen 1
        return {attr: entropies[attr] / total_entropy for attr in entropies}

    def export_graph(self, output_path: str, format: str = "gexf") -> None:
        if self.graph is None:
            return
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        G_export = self.graph.copy()
        for n, attrs in G_export.nodes(data=True):
            for k, v in list(attrs.items()):
                if v is None or v == "" or (isinstance(v, float) and np.isnan(v)):
                    G_export.nodes[n][k] = "NA"  # gephi no acepta vacios
        for u, v, attrs in G_export.edges(data=True):
            for k, val in list(attrs.items()):
                if val is None or (isinstance(val, float) and np.isnan(val)):
                    G_export.edges[u, v][k] = 0.0
        try:
            if format == "gexf":
                nx.write_gexf(G_export, output_path, encoding="utf-8")
            elif format == "graphml":
                nx.write_graphml(G_export, output_path)
        except Exception as e:
            print(f"[GraphBuilder] Error al exportar: {e}")
