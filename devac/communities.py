import networkx as nx
from networkx.algorithms import community
from collections import defaultdict
from typing import Dict, List, Tuple


class CommunityDetector:
    def __init__(self):
        self.event_communities = {}
        self.event_to_community = {}
        self.policies_by_community = defaultdict(set)

    def detect_communities(self, graph: nx.Graph) -> Tuple[Dict[str, int], Dict[str, int]]:
        # louvain, ojo: no es determinista
        communities = community.louvain_communities(graph, weight="weight", resolution=1)
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
