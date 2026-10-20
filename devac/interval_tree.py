import random
from typing import List, Optional, Set, Tuple


def _to_minutes(time_str: str) -> Optional[int]:
    try:
        h, m = time_str.strip().split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _is_time_format(value: str) -> bool:
    return _to_minutes(value) is not None


class _Node:
    __slots__ = ("start", "end", "community_id", "policy_id", "priority", "left", "right", "max_end")

    def __init__(self, start: int, end: int, community_id: str, policy_id: str):
        self.start = start
        self.end = end
        self.community_id = community_id
        self.policy_id = policy_id
        self.priority = random.random()
        self.left: Optional["_Node"] = None
        self.right: Optional["_Node"] = None
        self.max_end = end


def _update(node: _Node) -> None:
    m = node.end
    if node.left is not None and node.left.max_end > m:
        m = node.left.max_end
    if node.right is not None and node.right.max_end > m:
        m = node.right.max_end
    node.max_end = m


def _rotate_right(node: _Node) -> _Node:
    new_root = node.left
    node.left = new_root.right
    new_root.right = node
    _update(node)
    _update(new_root)
    return new_root


def _rotate_left(node: _Node) -> _Node:
    new_root = node.right
    node.right = new_root.left
    new_root.left = node
    _update(node)
    _update(new_root)
    return new_root


def _insert(node: Optional[_Node], new_node: _Node) -> _Node:
    if node is None:
        return new_node
    if new_node.start < node.start:
        node.left = _insert(node.left, new_node)
        if node.left.priority > node.priority:
            node = _rotate_right(node)
        else:
            _update(node)
    else:
        node.right = _insert(node.right, new_node)
        if node.right.priority > node.priority:
            node = _rotate_left(node)
        else:
            _update(node)
    return node


def _query_point(node: Optional[_Node], point: int, out: List[_Node]) -> None:
    if node is None or node.max_end < point:
        return  # todo el subarbol queda descartado: ningun intervalo aqui puede cubrir el punto
    if node.left is not None:
        _query_point(node.left, point, out)
    if node.start <= point <= node.end:
        out.append(node)
    if node.start <= point and node.right is not None:
        _query_point(node.right, point, out)


def _inorder(node: Optional[_Node], out: List[_Node]) -> None:
    if node is None:
        return
    _inorder(node.left, out)
    out.append(node)
    _inorder(node.right, out)


class IntervalTree:
    """
    Árbol de intervalos (Treap aumentado) para rangos horarios HH:MM.

    Estructura: BST balanceado por prioridad aleatoria (treap), ordenado
    por `start`, donde cada nodo guarda además `max_end` = el mayor `end`
    de todo su subárbol. Esa cota permite podar subárboles completos
    durante la consulta en vez de recorrer todos los intervalos cuyo
    inicio es <= al punto buscado.

    Complejidad esperada: O(log n) de altura (por las prioridades
    aleatorias) + O(k) para reportar los k intervalos que sí contienen
    el punto — a diferencia de un arreglo ordenado + bisección, que
    sigue siendo O(right) donde `right` crece con n aunque pocos de
    esos intervalos realmente se solapen con el punto.
    """

    def __init__(self):
        self._root: Optional[_Node] = None
        self._count = 0

    def insert(self, start_hhmm: str, end_hhmm: str, community_id: str, policy_id: str) -> bool:
        start = _to_minutes(start_hhmm)
        end = _to_minutes(end_hhmm)
        if start is None or end is None:
            return False
        self._root = _insert(self._root, _Node(start, end, community_id, policy_id))
        self._count += 1
        return True

    def remove_policy(self, policy_id: str) -> None:
        nodes: List[_Node] = []
        _inorder(self._root, nodes)
        kept = [n for n in nodes if n.policy_id != policy_id]

        self._root = None
        self._count = 0
        # reinsertar en orden aleatorio para evitar degenerar el treap
        random.shuffle(kept)
        for n in kept:
            self.insert_raw(n.start, n.end, n.community_id, n.policy_id)

    def insert_raw(self, start: int, end: int, community_id: str, policy_id: str) -> None:
        """Igual que insert(), pero recibe minutos ya calculados (uso interno)."""
        self._root = _insert(self._root, _Node(start, end, community_id, policy_id))
        self._count += 1

    def query(self, time_hhmm: str) -> Set[str]:
        point = _to_minutes(time_hhmm)
        if point is None:
            return set()
        matches: List[_Node] = []
        _query_point(self._root, point, matches)
        return {n.community_id for n in matches}

    def query_with_policies(self, time_hhmm: str) -> List[Tuple[str, str]]:
        point = _to_minutes(time_hhmm)
        if point is None:
            return []
        matches: List[_Node] = []
        _query_point(self._root, point, matches)
        return [(n.community_id, n.policy_id) for n in matches]

    def clear(self) -> None:
        self._root = None
        self._count = 0

    def __len__(self) -> int:
        return self._count

    def __repr__(self) -> str:
        return f"IntervalTree({self._count} intervals)"
