import json
from pathlib import Path

from devac.loader import PolicyLoader
from experiments.run_dynamics import build_plan, main

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
            pid = change["policy"]["policy_id"]
            assert pid in inactive and change["policy"]["events"]
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


# dos tareas de slurm (--rep) juntadas por resumen_e4 dan lo mismo que una corrida con --reps 2
def test_repeticiones_sueltas_y_resumen_e4(tmp_path, capsys):
    import importlib.util

    common = ["--policies", POLICIES, "--scenario", "deactivation", "--changes", "30", "--stress-threshold", "0.02"]
    assert main(common + ["--reps", "2", "--out-dir", str(tmp_path / "junta")]) == 0
    for rep in ("1", "2"):
        assert main(common + ["--rep", rep, "--out-dir", str(tmp_path / "suelta")]) == 0

    spec = importlib.util.spec_from_file_location("resumen_e4", ROOT / "clusterExperimentos/E4/resumen_e4.py")
    resumen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(resumen)
    [(config, merged)] = resumen.merge(tmp_path / "suelta")
    whole = json.loads((tmp_path / "junta" / "deactivation_rebuild_meta.json").read_text())

    assert config == "deactivation_rebuild" and merged["files"] == whole["files"]
    for name in whole["files"]:
        a = json.loads((tmp_path / "junta" / name).read_text())
        b = json.loads((tmp_path / "suelta" / name).read_text())
        assert _without_times(a) == _without_times(b)
    assert [r["louvain_seed"] for r in merged["replications"]] == [r["louvain_seed"] for r in whole["replications"]]
    assert [r["rebuilds"] for r in merged["replications"]] == [r["rebuilds"] for r in whole["replications"]]

    # y make_charts lee lo que quedo junto
    spec = importlib.util.spec_from_file_location("make_charts", ROOT / "graficas/make_charts.py")
    charts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(charts)
    charts.main(["--data-dir", str(tmp_path / "suelta"), "--out-dir", str(tmp_path / "figuras")])
    assert (tmp_path / "figuras" / "fig_trayectoria_estres.png").exists()
    capsys.readouterr()
