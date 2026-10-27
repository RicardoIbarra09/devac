import json
from pathlib import Path

from devac.graph import GraphBuilder
from devac.loader import PolicyLoader

ROOT = Path(__file__).resolve().parents[1]
POLICIES = ROOT / "experiments/nimbuslab_policies.json"
# grafo generado con build_event_graph antes de instrumentarlo
REFERENCE = ROOT / "tests/data/nimbuslab_graph_ref.json"

STATS_KEYS = {
    "t_weights_s", "t_nodes_s", "t_index_s", "t_pairs_s", "t_total_s",
    "nodes", "edges", "pairs_compared", "pairs_total", "buckets", "max_bucket", "omitibles",
}


def _build(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # si exportara, el gexf caeria aqui
    builder = GraphBuilder()
    graph = builder.build_event_graph(PolicyLoader.from_file(str(POLICIES)), export=False)
    return builder, graph


def test_grafo_igual_al_de_antes(tmp_path, monkeypatch):
    _, graph = _build(tmp_path, monkeypatch)
    ref = json.loads(REFERENCE.read_text(encoding="utf-8"))

    assert {n: d for n, d in graph.nodes(data=True)} == ref["nodes"]
    edges = sorted(sorted([u, v]) + [d["weight"]] for u, v, d in graph.edges(data=True))
    assert edges == ref["edges"]  # mismas aristas y mismos pesos


def test_export_false_no_escribe_el_gexf(tmp_path, monkeypatch):
    _build(tmp_path, monkeypatch)
    assert not (tmp_path / "exports").exists()


def test_last_stats(tmp_path, monkeypatch):
    builder, graph = _build(tmp_path, monkeypatch)
    stats = builder.last_stats

    assert set(stats) == STATS_KEYS
    assert stats["nodes"] == graph.number_of_nodes() and stats["edges"] == graph.number_of_edges()
    assert stats["pairs_total"] == stats["nodes"] * (stats["nodes"] - 1) // 2
    assert stats["edges"] <= stats["pairs_compared"] <= stats["pairs_total"]
    assert stats["max_bucket"] >= 2 if stats["buckets"] else stats["max_bucket"] == 0
    phases = stats["t_weights_s"] + stats["t_nodes_s"] + stats["t_index_s"] + stats["t_pairs_s"]
    assert stats["t_total_s"] == phases and all(stats[k] >= 0 for k in STATS_KEYS if k.startswith("t_"))
    assert isinstance(stats["omitibles"], list)


def test_last_stats_vacio_antes_del_primer_build():
    assert GraphBuilder().last_stats == {}
