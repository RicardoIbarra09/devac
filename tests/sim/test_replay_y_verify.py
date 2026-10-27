import asyncio
import csv
from dataclasses import asdict

import httpx
import pytest

from devac.models import Policy
from devac_api.main import create_app
from devac_client.loadgen import build_schedule, load_workload, run_load, split_shards, summarize
from devac_sim.generator import TraceParams, generate_trace
from devac_sim.oracle import Oracle, load_rules
from devac_sim.trace import write_trace
from devac_sim.verify import read_results, verify
from tests.api.conftest import BASE_POLICIES, build_engine, event, policy, request

RULES = "devac_api.plugins.example_rules"


def _mixed_trace(tmp_path, **params):
    meta, records = generate_trace(BASE_POLICIES, TraceParams(**params))
    path = tmp_path / "traza.jsonl"
    write_trace(str(path), meta, records)
    return path, records


def test_traza_mixta_solo_con_speed(tmp_path):
    path, records = _mixed_trace(tmp_path, window=2, rate=20, mutation_rate=3, seed=1)
    items = load_workload(str(path))
    assert len(items) == len(records)
    with pytest.raises(ValueError, match="--speed"):
        build_schedule(items, len(items), rate=0, speed=0)
    with pytest.raises(ValueError, match="repetir"):
        build_schedule(items, 2 * len(items), rate=0, speed=1)


def test_reparto_entre_procesos_deja_el_control_en_el_primero():
    control = {"kind": "policy_delete", "policy_id": "P"}
    schedule = [(0.0, request()), (0.1, control), (0.2, request()), (0.3, control), (0.4, request())]
    assert split_shards(schedule, 2) == [[0, 1, 3, 4], [2]]


def test_replay_mixto_y_verify_contra_el_oraculo(tmp_path):
    turno = policy("P_TURNO", events=[event("E_T", subject="enfermera", action="escribir")], reconfigure_on=["sesion_terminada"])
    base = BASE_POLICIES + [turno]
    meta, records = generate_trace(base, TraceParams(window=1.0, rate=300, mutation_rate=15, event_rate=5, seed=3))
    path = tmp_path / "traza.jsonl"
    write_trace(str(path), meta, records)

    # sin auditoria: con ella P_TURNO pierde su evento (ver test_auditoria_no_debe_perder_eventos)
    engine = build_engine(base, rules_module=RULES, audit=False)
    transport = httpx.ASGITransport(app=create_app(engine=engine))
    items = load_workload(str(path))
    schedule = build_schedule(items, len(items), rate=0, speed=20)  # 20x para que la prueba sea corta
    samples, duration = asyncio.run(run_load("http://devac", schedule, concurrency=8, transport=transport))

    summary = summarize(samples, duration)
    controls = [s for s in samples if s.kind != "access"]
    assert summary["errors"] == 0 and summary["control_errors"] == 0
    assert [s.policy_version for s in controls] == list(range(1, len(controls) + 1))  # en orden

    results_path = tmp_path / "replay.csv"
    with results_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(samples[0])))
        writer.writeheader()
        writer.writerows(asdict(s) for s in samples)

    oracle = Oracle([Policy.model_validate(p) for p in base], load_rules(RULES))
    report = verify(records, read_results(str(results_path)), oracle)
    assert report["incorrect"] == 0 and report["incorrect_in_transition"] == 0
    assert report["checked"] + report["in_transition"] == summary["requests"]
    assert report["unmapped_version"] == 0 and report["controls_failed"] == 0


def test_verify_detecta_una_decision_equivocada():
    records = [
        {"t": 0.0, "kind": "access", "request": request()},
        {"t": 0.1, "kind": "policy_delete", "policy_id": "P_MEDICO"},
        {"t": 0.2, "kind": "access", "request": request()},
    ]
    results = {
        0: {"index": "0", "status": "200", "decision": "allow", "policy_version": "7", "consistent": "True"},
        1: {"index": "1", "status": "204", "decision": "", "policy_version": "8", "consistent": ""},
        2: {"index": "2", "status": "200", "decision": "allow", "policy_version": "8", "consistent": "True"},
    }
    report = verify(records, results, Oracle([Policy.model_validate(p) for p in BASE_POLICIES]))
    assert report["checked"] == 2 and report["incorrect"] == 1
    assert report["mismatches"][0]["index"] == 2
