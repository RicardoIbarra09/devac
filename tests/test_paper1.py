import csv
import importlib.util
import json
from pathlib import Path

from paper1_dinamica.run_dynamics import main as run_dynamics

ROOT = Path(__file__).resolve().parents[1]
P1 = ROOT / "paper1_dinamica"
POLICIES = "experiments/nimbuslab_policies.json"


def _load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(tmp, *args):
    assert run_dynamics(["--policies", POLICIES, *args, "--out-dir", str(tmp)]) == 0


# un arbol resultados/crudo/dN/<job>/... chico, como el que dejan los sbatch
def _fake_results(raw):
    d1 = _load(P1 / "D1_exactitud" / "run_d1.py")
    for scenario in ("deactivation", "mixed"):
        assert d1.main(["--policies", POLICIES, "--scenario", scenario, "--changes", "30", "--rep", "1",
                        "--check-graph-every", "10", "--out-dir", str(raw / "d1/101/nimbus")]) == 0
    for rep in ("1", "2"):
        _run(raw / "d2/102/n100", "--scenario", "mixed", "--changes", "20", "--stress-threshold", "1e9",
             "--tag", "norebuild", "--rep", rep)
        for scenario in ("deactivation", "mixed"):
            _run(raw / "d3/103/nimbus", "--scenario", scenario, "--changes", "30", "--stress-threshold", "0.02",
                 "--rep", rep)
            _run(raw / "d3/103/nimbus", "--scenario", scenario, "--changes", "30", "--stress-threshold", "1e9",
                 "--rep", rep)
        for theta in ("0.01", "0.05"):
            _run(raw / "d4/104/nimbus", "--scenario", "deactivation", "--changes", "30", "--stress-threshold", theta,
                 "--tag", f"theta{theta}", "--networkx-sample", "0", "--rep", rep)


def test_d1_a_d4_resumenes_y_figuras(tmp_path, capsys):
    raw = tmp_path / "crudo"
    _fake_results(raw)

    out = tmp_path / "resultados"
    _load(P1 / "D1_exactitud" / "resumen_d1.py").main([str(raw / "d1/101"), str(out / "d1.csv")])
    with open(out / "d1.csv", newline="") as f:
        d1 = list(csv.DictReader(f))
    assert len(d1) == 2
    for row in d1:
        assert float(row["max_abs_q_incremental_vs_networkx"]) < 1e-9
        assert int(row["edge_mismatches"]) == 0 and int(row["node_mismatches"]) == 0
        assert float(row["max_abs_q_vs_rebuilt_graph"]) < 1e-9 and int(row["graph_checks"]) == 3

    for exp, folder in (("d2", "D2_escalamiento"), ("d3", "D3_dinamica"), ("d4", "D4_sensibilidad")):
        job = next((raw / exp).iterdir())
        resumen = _load(P1 / folder / f"resumen_{exp}.py")
        assert resumen.main([str(job), str(out / f"{exp}.csv")]) == 0
    with open(out / "d4.csv", newline="") as f:
        d4 = list(csv.DictReader(f))
    assert {row["theta"] for row in d4} == {"0.01", "0.05"} and len(d4) == 4

    figures = _load(P1 / "figuras" / "make_figures.py")
    figures.main(["--d1", str(raw / "d1/101"), "--d2", str(raw / "d2/102"), "--d3", str(raw / "d3/103"),
                  "--d4", str(raw / "d4/104"), "--d2-events", "100", "--out-dir", str(tmp_path / "figuras")])
    for name in ("fig_d1_exactitud", "fig_d2_escalamiento", "fig_d2_costo_en_el_tiempo_n100",
                 "fig_d3_estres_nimbus", "fig_d3_q_nimbus", "fig_d4_sensibilidad"):
        assert (tmp_path / "figuras" / f"{name}.png").exists(), name
    capsys.readouterr()


# la misma configuracion dos veces: la comparacion de reproducibilidad da cero
def test_compara_repro(tmp_path, capsys):
    for job in ("201", "202"):
        _run(tmp_path / job / "nimbus", "--scenario", "deactivation", "--changes", "20",
             "--stress-threshold", "0.02", "--rep", "1")
    compara = _load(P1 / "D3_dinamica" / "compara_repro.py")
    assert compara.main([str(tmp_path / "201"), str(tmp_path / "202"), "--csv", str(tmp_path / "repro.csv")]) == 0
    with open(tmp_path / "repro.csv", newline="") as f:
        [row] = list(csv.DictReader(f))
    assert float(row["max_abs_q_incremental"]) == 0.0 and int(row["rebuilds_mismatches"]) == 0
    assert row["rows_a"] == row["rows_b"] == "20"
    capsys.readouterr()


def test_meta_de_d1_marca_los_tiempos(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("TIMING", "1")
    _run(tmp_path, "--scenario", "deactivation", "--changes", "5", "--rep", "1")
    meta = json.loads((tmp_path / "deactivation_rebuild_r01_meta.json").read_text())
    assert meta["timing"] == "reported" and "timing_note" not in meta
    capsys.readouterr()
