import importlib.util
from pathlib import Path

import pytest

from devac.graph import GraphBuilder

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "clusterExperimentos/E1/run_e1.py"
POLICIES = str(ROOT / "experiments/nimbuslab_policies.json")


@pytest.fixture
def run_e1(monkeypatch):
    # el script apaga export_graph al importarse; que no se quede asi para las demas pruebas
    monkeypatch.setattr(GraphBuilder, "export_graph", GraphBuilder.export_graph)
    spec = importlib.util.spec_from_file_location("run_e1", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_con_auditoria_el_evaluador_excluye_y_no_cambia_decisiones(run_e1, capsys):
    row = run_e1.run_one("W", POLICIES, seed=1, n_requests=400, audit_on=True)
    assert row["excluded_events"] >= 1  # nimbuslab tiene al menos un evento cubierto por otro
    assert row["agree_devac_linear_pct"] == 100.0
    assert row["agree_devac_oracle_pct"] == 100.0
    assert row["decisions_preserved_pct"] == 100.0
    capsys.readouterr()


def test_sin_auditoria_no_hay_columna_de_excluidos(run_e1, capsys):
    row = run_e1.run_one("W", POLICIES, seed=1, n_requests=200, audit_on=False)
    assert row["excluded_events"] == "" and row["agree_devac_linear_pct"] == 100.0
    capsys.readouterr()
