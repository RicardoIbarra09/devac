import json
import random

import pytest

from devac_sim.arrivals import arrival_times
from devac_sim.generator import TraceParams, generate_trace
from devac_sim.oracle import Oracle
from devac_sim.trace import ACCESS, TraceError, read_trace, write_trace
from devac.models import Policy
from tests.api.conftest import BASE_POLICIES, event, policy


def test_poisson_respeta_la_tasa():
    times = arrival_times("poisson", rate=500, window=20, rng=random.Random(1))
    assert abs(len(times) - 10_000) < 400  # ~4 desviaciones estandar
    assert times == sorted(times) and times[-1] < 20


def test_uniform_es_una_cada_1_entre_rate():
    times = arrival_times("uniform", rate=4, window=1, rng=random.Random(1))
    assert times == [0.0, 0.25, 0.5, 0.75]


def test_rafaga_conserva_la_tasa_media():
    times = arrival_times("rafaga", rate=200, window=200, rng=random.Random(3), burst_factor=4, burst_fraction=0.2)
    assert abs(len(times) / 200 - 200) < 20


def test_pico_concentra_llegadas_a_la_mitad():
    times = arrival_times("pico", rate=100, window=80, rng=random.Random(5), peak_factor=4)
    centro = sum(1 for t in times if 35 <= t < 45)
    orilla = sum(1 for t in times if t < 10)
    assert centro > 2 * orilla


def test_traza_ida_y_vuelta(tmp_path):
    path = tmp_path / "t.jsonl"
    records = [{"t": 0.0, "kind": ACCESS, "request": {"subject": "a", "asset": "b", "space": "c", "time": "10:00", "action": "d"}},
               {"t": 0.5, "kind": "policy_delete", "policy_id": "P1"}]
    write_trace(str(path), {"seed": 3}, records)
    meta, leidos = read_trace(str(path))
    assert meta["seed"] == 3 and leidos == records


@pytest.mark.parametrize("linea", [
    {"t": 1.0, "kind": "otro"},
    {"t": -1, "kind": "policy_delete", "policy_id": "P"},
    {"t": 1.0, "kind": "policy_add"},
    {"t": 1.0, "kind": ACCESS, "request": {"subject": "a"}},
])
def test_traza_invalida(tmp_path, linea):
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps(linea) + "\n")
    with pytest.raises(TraceError):
        read_trace(str(path))


def test_traza_desordenada(tmp_path):
    path = tmp_path / "t.jsonl"
    lines = [{"t": 2.0, "kind": "policy_delete", "policy_id": "A"}, {"t": 1.0, "kind": "policy_delete", "policy_id": "B"}]
    path.write_text("\n".join(json.dumps(x) for x in lines))
    with pytest.raises(TraceError, match="ordenada"):
        read_trace(str(path))


def test_misma_semilla_misma_traza():
    params = TraceParams(window=5, rate=50, mutation_rate=2, seed=9)
    assert generate_trace(BASE_POLICIES, params) == generate_trace(BASE_POLICIES, params)
    otra = TraceParams(window=5, rate=50, mutation_rate=2, seed=10)
    assert generate_trace(BASE_POLICIES, params)[1] != generate_trace(BASE_POLICIES, otra)[1]


def test_cambiar_mutaciones_no_mueve_las_llegadas():
    quieta = generate_trace(BASE_POLICIES, TraceParams(window=5, rate=50, seed=4))[1]
    movida = generate_trace(BASE_POLICIES, TraceParams(window=5, rate=50, mutation_rate=3, seed=4))[1]
    assert [r["t"] for r in quieta] == [r["t"] for r in movida if r["kind"] == ACCESS]


def test_mutaciones_validas_contra_el_oraculo():
    _, records = generate_trace(BASE_POLICIES, TraceParams(window=30, rate=5, mutation_rate=4, seed=2))
    oracle = Oracle([Policy.model_validate(p) for p in BASE_POLICIES])
    for r in records:
        if r["kind"] != ACCESS:
            oracle.apply(r)
    assert oracle.errors == []  # nunca toca politicas que ya no existen
    assert oracle.version == sum(1 for r in records if r["kind"] != ACCESS)


def test_match_fraction_uno_siempre_cubre():
    _, records = generate_trace(BASE_POLICIES, TraceParams(window=2, rate=200, match_fraction=1.0, seed=1))
    oracle = Oracle([Policy.model_validate(p) for p in BASE_POLICIES])
    assert all(oracle.decide(r["request"])[1] for r in records)


def test_eventos_de_dominio_solo_si_alguien_los_declara():
    meta, records = generate_trace(BASE_POLICIES, TraceParams(window=10, rate=1, event_rate=2, seed=1))
    assert not any(r["kind"] == "domain_event" for r in records)
    assert meta["skipped_domain_events"] > 0

    con_evento = [policy("P_TURNO", events=[event("E_T")], reconfigure_on=["sesion_terminada"])]
    _, records = generate_trace(con_evento, TraceParams(window=10, rate=1, event_rate=2, seed=1))
    eventos = [r["event"] for r in records if r["kind"] == "domain_event"]
    assert eventos and all(e["event_type"] == "sesion_terminada" and "subject_id" in e for e in eventos)
