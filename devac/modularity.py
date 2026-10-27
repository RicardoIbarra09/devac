from collections import defaultdict
from typing import Dict, Hashable, Mapping

import networkx as nx


# Q = sum(L_c)/m - sum(D_c^2)/(4 m^2), con pesos de arista y resolution=1, igual que networkx.
# L_c: peso de las aristas dentro de c; D_c: suma de grados ponderados de c; m: peso total.
# solo cuentan los nodos con comunidad y las aristas entre ellos (mismo criterio que el monitor
# usa con networkx, que toma el subgrafo de los nodos cubiertos)
class IncrementalModularity:
    def __init__(self):
        self.community: Dict[str, Hashable] = {}  # nodo -> comunidad
        self.internal: Dict[Hashable, float] = defaultdict(float)  # L_c
        self.degree: Dict[Hashable, float] = defaultdict(float)  # D_c
        self.size: Dict[Hashable, int] = defaultdict(int)
        self.m = 0.0
        self.edges = 0  # para dejar m en cero exacto cuando ya no hay aristas
        self.sum_internal = 0.0
        self.sum_degree_sq = 0.0

    @classmethod
    def from_partition(cls, graph: nx.Graph, node_community: Mapping[str, Hashable]) -> "IncrementalModularity":
        state = cls()
        for node, comm in node_community.items():
            if node in graph:
                state.add_node(graph, node, comm)
        return state

    @property
    def q(self) -> float:
        if self.edges == 0:
            return 0.0
        return self.sum_internal / self.m - self.sum_degree_sq / (4.0 * self.m * self.m)

    def __contains__(self, node: str) -> bool:
        return node in self.community

    # el nodo ya debe estar en el grafo con sus aristas; solo se recorren sus vecinos
    def add_node(self, graph: nx.Graph, node: str, comm: Hashable) -> None:
        if node in self.community:
            raise ValueError(f"el nodo {node} ya tiene comunidad")
        changes: Dict[Hashable, float] = defaultdict(float)
        for neighbor, data in graph[node].items():
            neighbor_comm = self.community.get(neighbor)
            if neighbor_comm is None:
                continue  # vecino sin comunidad: su arista no cuenta
            w = data.get("weight", 1.0)
            self.m += w
            self.edges += 1
            changes[comm] += w
            changes[neighbor_comm] += w
            if neighbor_comm == comm:
                self.internal[comm] += w
                self.sum_internal += w
        self.community[node] = comm
        self.size[comm] += 1
        self._apply(changes)

    # se llama antes de quitar el nodo del grafo, porque hacen falta sus aristas
    def remove_node(self, graph: nx.Graph, node: str) -> None:
        comm = self.community.pop(node, None)
        if comm is None:
            return
        changes: Dict[Hashable, float] = defaultdict(float)
        for neighbor, data in graph[node].items():
            neighbor_comm = self.community.get(neighbor)
            if neighbor_comm is None:
                continue
            w = data.get("weight", 1.0)
            self.m -= w
            self.edges -= 1
            changes[comm] -= w
            changes[neighbor_comm] -= w
            if neighbor_comm == comm:
                self.internal[comm] -= w
                self.sum_internal -= w
        self._apply(changes)
        self.size[comm] -= 1
        if self.size[comm] == 0:
            # comunidad vacia: se descarta lo que quede de redondeo
            self.sum_degree_sq -= self.degree.pop(comm, 0.0) ** 2
            self.sum_internal -= self.internal.pop(comm, 0.0)
            del self.size[comm]
        if self.edges == 0:
            self.m = 0.0
            self.sum_internal = 0.0
            self.sum_degree_sq = 0.0
            for c in self.degree:
                self.degree[c] = 0.0
            for c in self.internal:
                self.internal[c] = 0.0

    def _apply(self, changes: Mapping[Hashable, float]) -> None:
        for comm, delta in changes.items():
            before = self.degree[comm]
            after = before + delta
            self.sum_degree_sq += after * after - before * before
            self.degree[comm] = after
