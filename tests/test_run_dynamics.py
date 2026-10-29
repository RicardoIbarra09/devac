import json
from pathlib import Path

from devac.loader import PolicyLoader
from paper1_dinamica.run_dynamics import build_plan, main, networkx_changes

ROOT = Path(__file__).resolve().parents[1]
POLICIES = "experiments/nimbuslab_policies.json"


def _policies():
    return [p.model_dump() for p in PolicyLoader.from_file(str(ROOT / POLICIES))]


def test_plan_mixto_reactiva_lo_que_desactivo():
    plan = build_plan(_policies(), "mixed", 300, seed=2)
    assert build_plan(_policies(), "mixed", 300, seed=2) == plan  # misma semilla, mismo plan
    inactive = set()
    for change in plan:
        if change["change"] == "deactivate":
            inactive.add(change["policy_id"])
        elif change["change"] == "reactivate":
            pid = change["policy_id"]
            assert pid in inactive and "policy" not in change  # la politica guarda sus eventos
            inactive.remove(pid)
    assert {c["change"] for c in plan} == {"add", "update", "remove", "deactivate", "reactivate"}


def test_plan_de_desactivacion_no_repite_politicas():
    plan = build_plan(_policies(), "deactivation", 50, seed=1)
    assert len({c["policy_id"] for c in plan}) == 50


def test_corrida_escribe_filas_y_meta(tmp_path, capsys):
    args = ["--policies", POLICIES, "--scenario", "mixed", "--changes", "40", "--reps", "2",
            "--stress-threshold", "0.02", "--out-dir", str(tmp_path)]
    assert main(args) == 0
    meta = json.loads((tmp_path / "mixed_rebuild_meta.json").read_text())
    assert meta["files"] == ["mixed_rebuild_r01.json", "mixed_rebuild_r02.json"]
    assert meta["params"]["changes"] == 40 and meta["argv"] == args
    for name in meta["files"]:
        rows = json.loads((tmp_path / name).read_text())
        assert len(rows) == 40
        for row in rows:
            assert abs(row["q_incremental"] - row["q_networkx"]) < 1e-9
            assert {"t_networkx_s", "t_incremental_s", "t_graph_s", "rebuilds", "change"} <= set(row)
    assert all(r["max_abs_q_diff"] < 1e-9 for r in meta["replications"])
    capsys.readouterr()


TIMES = {"t_real_s", "t_networkx_s", "t_incremental_s", "t_graph_s"}


def _without_times(rows):
    return [{k: v for k, v in row.items() if k not in TIMES} for row in rows]


# dos tareas de slurm (--rep) leidas con datos.load_runs dan lo mismo que una corrida con --reps 2
def test_repeticiones_sueltas_igual_a_corrida_completa(tmp_path, capsys):
    from paper1_dinamica.datos import load_runs

    common = ["--policies", POLICIES, "--scenario", "deactivation", "--changes", "30", "--stress-threshold", "0.02"]
    assert main(common + ["--reps", "2", "--out-dir", str(tmp_path / "junta")]) == 0
    for rep in ("1", "2"):
        assert main(common + ["--rep", rep, "--out-dir", str(tmp_path / "suelta")]) == 0

    [(config, loose)] = load_runs(tmp_path / "suelta").items()
    whole = load_runs(tmp_path / "junta")["deactivation_rebuild"]
    assert config == "deactivation_rebuild" and len(loose["metas"]) == 2
    for a, b in zip(whole["reps"], loose["reps"]):
        assert a["file"] == b["file"] and _without_times(a["rows"]) == _without_times(b["rows"])
        assert a["summary"]["plan_seed"] == b["summary"]["plan_seed"]
        assert a["summary"]["louvain_seed"] == b["summary"]["louvain_seed"]
    capsys.readouterr()


# cada repeticion tiene su propia secuencia de cambios y su semilla de louvain, y quedan en el meta
def test_cada_repeticion_usa_otra_secuencia(tmp_path, capsys):
    args = ["--policies", POLICIES, "--scenario", "deactivation", "--changes", "20", "--reps", "2",
            "--stress-threshold", "1e9", "--out-dir", str(tmp_path)]
    assert main(args) == 0
    meta = json.loads((tmp_path / "deactivation_norebuild_meta.json").read_text())
    assert meta["plan_seeds"] == [1001, 1002] and meta["louvain_seeds"] == [1001, 1002]
    assert [r["plan_seed"] for r in meta["replications"]] == [1001, 1002]
    first, second = (json.loads((tmp_path / name).read_text()) for name in meta["files"])
    assert [r["policy_id"] for r in first] != [r["policy_id"] for r in second]
    assert {"commit", "dirty", "node", "cpu", "pip_freeze", "timing"} <= set(meta)
    assert meta["timing"] == "timing_not_reported"  # sin TIMING=1
    capsys.readouterr()


def test_muestra_de_q_completa(tmp_path, capsys):
    assert networkx_changes(10, None) == set(range(1, 11))
    assert networkx_changes(10, 0) == set()
    assert networkx_changes(100, 5) == {1, 21, 41, 61, 81}
    args = ["--policies", POLICIES, "--scenario", "mixed", "--changes", "30", "--rep", "1",
            "--networkx-sample", "4", "--out-dir", str(tmp_path)]
    assert main(args) == 0
    rows = json.loads((tmp_path / "mixed_rebuild_r01.json").read_text())
    measured = [r for r in rows if r["q_networkx"] is not None]
    assert len(measured) == 4 and all(r["t_networkx_s"] is not None for r in measured)
    meta = json.loads((tmp_path / "mixed_rebuild_r01_meta.json").read_text())
    assert meta["networkx_sample"]["changes_measured"] == 4
    assert meta["replications"][0]["networkx_changes"] == 4
    capsys.readouterr()
