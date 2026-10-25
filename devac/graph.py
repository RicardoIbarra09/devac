import networkx as nx
from devac.models import Event, Policy
from typing import List, Dict, Set, Tuple
from collections import Counter, defaultdict
import numpy as np
from pathlib import Path


class GraphBuilder:
    def __init__(self):
        self.graph = None

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

    def build_event_graph(self, policies: List[Policy], similarity_threshold: float = 0.3) -> nx.Graph:
        G = nx.Graph()
        attribute_weights = self.calculate_attribute_weights(policies)  # pesos por entropia

        # atributos que aunque coincidan nunca alcanzan el umbral
        omitibles = set()
        suma = 0.0
        for attr, peso in sorted(attribute_weights.items(), key=lambda kv: kv[1]):
            if suma + peso < similarity_threshold:
                suma += peso
                omitibles.add(attr)

        all_events_map = {}  # event_id -> evento y su politica

        for policy in policies:
            for event in policy.events:
                if isinstance(event.subject.value, str): event.subject.value = event.subject.value.strip().lower()
                if isinstance(event.asset.value,   str): event.asset.value   = event.asset.value.strip().lower()
                if isinstance(event.space.value,   str): event.space.value   = event.space.value.strip().lower()
                if isinstance(event.time.value,    str): event.time.value    = event.time.value.strip().lower()
                if isinstance(event.action.value,  str): event.action.value  = event.action.value.strip().lower()

                all_events_map[event.event_id] = {"obj": event, "policy_id": policy.policy_id}

                def _gephi(v):
                    return str(v) if isinstance(v, dict) else v

                # un nodo por evento
                G.add_node(
                    event.event_id,
                    policy_id     = policy.policy_id,
                    rol           = _gephi(event.subject.value),
                    recurso       = _gephi(event.asset.value),
                    ubicacion     = _gephi(event.space.value),
                    rango_horario = _gephi(event.time.value),
                    accion        = _gephi(event.action.value),
                )

        # la cubeta de tiempo se llama distinto que su peso
        _PREFIX_TO_WEIGHT_KEY = {
            "rol": "rol", "recurso": "recurso", "ubicacion": "ubicacion",
            "tiempo": "rango_horario", "accion": "accion",
        }

        inverted_index = defaultdict(list)  # valor -> eventos que lo usan
        for ev_id, data in all_events_map.items():
            ev = data["obj"]

            def _idx_val(prefix, val):
                # no vale la pena indexarlo
                if _PREFIX_TO_WEIGHT_KEY[prefix] in omitibles:
                    return
                if isinstance(val, str):
                    inverted_index[f"{prefix}:{val}"].append(ev_id)

            _idx_val("rol",       ev.subject.value)
            _idx_val("recurso",   ev.asset.value)
            _idx_val("ubicacion", ev.space.value)
            _idx_val("tiempo",    ev.time.value)
            _idx_val("accion",    ev.action.value)

        processed_pairs: Set[Tuple[str, str]] = set()  # para no comparar dos veces el mismo par

        # solo se comparan eventos de la misma cubeta
        for key, event_ids_in_bucket in inverted_index.items():
            if len(event_ids_in_bucket) < 2:
                continue
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

        self.graph = G
        self.export_graph("exports/event_graph.gexf")  # para abrirlo en gephi
        return self.graph

    @staticmethod
    def calculate_attribute_weights(policies: List[Policy]) -> dict:
        attribute_values = {
            "rol": [], "recurso": [], "ubicacion": [], "rango_horario": [], "accion": []
        }

        def _str(v):
            # in/range cuentan como un solo valor
            return v.strip().lower() if isinstance(v, str) else "__complex__"

        for policy in policies:
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
