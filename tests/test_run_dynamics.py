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
            assert {"t_networkx_s", "t_incremental_s", "t_proxy_s", "rebuilds", "change"} <= set(row)
    assert all(r["max_abs_q_diff"] < 1e-9 for r in meta["replications"])
    capsys.readouterr()
