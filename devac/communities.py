import random
import networkx as nx
from networkx.algorithms import community
from collections import defaultdict
from typing import Dict, List, Optional, Tuple


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

    # pasa de comunidad por evento a comunidad por politica
    def map_policies_to_communities(
        self,
        event_to_policy: Dict[str, str],
        event_to_community: Dict[str, int],
    ) -> Dict[str, List[str]]:
        self.policies_by_community = defaultdict(set)
        for event_id, community_id in event_to_community.items():
            policy_id = event_to_policy.get(event_id)
            if policy_id is not None:
                self.policies_by_community[community_id].add(policy_id)
        self.policies_by_community = {
            cid: list(pids) for cid, pids in self.policies_by_community.items()
        }
        return self.policies_by_community
