import math
import random
import re
from numbers import Real
import networkx as nx
from networkx.algorithms import community
from collections import defaultdict
from typing import Dict, Hashable, List, Mapping, Optional, Tuple

# dos puntajes que solo difieren por el redondeo de sumar en otro orden cuentan como empate
TIE_REL_TOL = 1e-12


# orden de los ids de comunidad: primero los numericos por valor, luego los de texto en orden
# natural (c5 antes que c10). nunca compara un numero con un texto
def community_order(comm: Hashable) -> tuple:
    if isinstance(comm, Real):
        return (0, comm)
    text = str(comm)
    parts = re.split(r"([0-9]+)", text)  # posiciones pares texto, impares digitos
    return (1, tuple(int(t) if i % 2 else t for i, t in enumerate(parts)), text)


# la comunidad con mayor puntaje; si empatan, la de menor id
def best_scored_community(scores: Mapping[Hashable, float]) -> Hashable:
    top = max(scores.values())
    tied = [c for c, score in scores.items() if math.isclose(score, top, rel_tol=TIE_REL_TOL)]
    return min(tied, key=community_order)


class CommunityDetector:
    # seed: cada llamada a louvain toma su semilla de este generador, asi una corrida se puede repetir
    def __init__(self, seed: Optional[int] = None):
        self._rng = random.Random(seed) if seed is not None else None
        self.event_communities = {}
        self.event_to_community = {}
        self.policies_by_community = defaultdict(set)

    # semilla para la siguiente corrida de louvain; None si el detector no tiene generador
    def next_seed(self) -> Optional[int]:
        return self._rng.randrange(2**32) if self._rng is not None else None

    # seed: la de esta corrida; si no se pasa, sale del generador del detector
    def detect_communities(self, graph: nx.Graph, seed: Optional[int] = None) -> Tuple[Dict[str, int], Dict[str, int]]:
        # louvain, ojo: sin semilla no es determinista
        if seed is None:
            seed = self.next_seed()
        communities = community.louvain_communities(graph, weight="weight", resolution=1, seed=seed)
        self.event_communities = {}
        self.event_to_community = {}
        for community_id, nodes in enumerate(communities):
            for node_id in nodes:
                self.event_communities[node_id] = community_id
                self.event_to_community[node_id] = community_id
        return self.event_communities, self.event_to_community

    # pasa de comunidad por evento a comunidad por politica. cada politica queda en una sola: la que
    # junta mas grado ponderado de sus eventos en el grafo (el que se le dio a louvain)
    def map_policies_to_communities(
        self,
        event_to_policy: Dict[str, str],
        event_to_community: Dict[str, int],
        graph: nx.Graph,
    ) -> Dict[str, List[str]]:
        found: Dict[str, Dict[int, List[str]]] = defaultdict(lambda: defaultdict(list))
        for event_id, community_id in event_to_community.items():
            policy_id = event_to_policy.get(event_id)
            if policy_id is not None:
                found[policy_id][community_id].append(event_id)

        chosen = {}
        for policy_id, events_by_comm in found.items():
            if len(events_by_comm) == 1:
                chosen[policy_id] = next(iter(events_by_comm))
            else:
                chosen[policy_id] = best_scored_community({
                    comm: math.fsum(graph.degree(e, weight="weight") for e in event_ids)
                    for comm, event_ids in events_by_comm.items()
                })

        self.policies_by_community = defaultdict(set)
        for event_id, community_id in event_to_community.items():
            policy_id = event_to_policy.get(event_id)
            if policy_id is not None and chosen[policy_id] == community_id:
                self.policies_by_community[community_id].add(policy_id)
        self.policies_by_community = {
            cid: list(pids) for cid, pids in self.policies_by_community.items()
        }
        return self.policies_by_community
