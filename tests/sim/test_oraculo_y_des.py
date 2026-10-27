import pytest

from devac.models import Policy
from devac_api.plugins.example_rules import deny_when_session_ends
from devac_sim.des import SimConfig, simulate
from devac_sim.oracle import Oracle
from devac_sim.stats import confidence_interval, summarize, time_series
from tests.api.conftest import BASE_POLICIES, event, policy, request

RULES = "devac_api.plugins.example_rules"


def _policies(raw):
    return [Policy.model_validate(p) for p in raw]


def _access(t, **values):
    return {"t": t, "kind": "access", "request": request(**values)}


def test_oraculo_deny_gana_y_deny_por_defecto():
    oracle = Oracle(_policies(BASE_POLICIES))
    assert oracle.decide(request()) == ("allow", True)
    assert oracle.decide(request(asset="nomina")) == ("deny", True)
    assert oracle.decide(request(subject="visitante")) == ("deny", False)
    assert oracle.decide(request(subject="guardia", time="23:30"))[0] == "allow"


def test_oraculo_responde_en_cualquier_version():
    oracle = Oracle(_policies(BASE_POLICIES))
    oracle.apply({"t": 1.0, "kind": "policy_delete", "policy_id": "P_MEDICO"})
    assert oracle.version == 1
    assert oracle.decide(request(), version=0)[0] == "allow"
    assert oracle.decide(request())[0] == "deny"


def test_oraculo_aplica_reglas_y_cascada():
    turno = policy("P_TURNO", events=[event("E_T")], reconfigure_on=["sesion_terminada"])
    vigilante = policy("P_VIG", events=[event("E_V", subject="auditor")], reconfigure_on=["politica_actualizada"])
    vistos = []

    def anotar(p, ev):
        vistos.append(ev.payload.get("cascade_depth"))
        return None

    rules = {"sesion_terminada": [deny_when_session_ends], "politica_actualizada": [anotar]}
    oracle = Oracle(_policies([turno, vigilante]), rules)
    oracle.apply({"t": 0.0, "kind": "domain_event", "event": {"event_type": "sesion_terminada", "subject_id": "medico"}})
    assert oracle.decide(request())[0] == "deny"
    assert oracle.reconfigurations == 1
    assert vistos == [1]  # la cascada llego con profundidad 1


# servicio fijo de 1 ms: la cola se puede calcular a mano
FIXED = dict(service_scale=0.0, overhead_us=1000.0)


def test_des_fifo_con_servicio_determinista():
    records = [_access(0.0), _access(0.0), _access(0.0), _access(0.0105)]
    result = simulate(records, _policies(BASE_POLICIES), SimConfig(servers=1, **FIXED))
    waits = [round(r["wait_us"]) for r in result.rows]
    assert waits == [0, 1000, 2000, 0]
    assert [r["server"] for r in result.rows] == [0, 0, 0, 0]

    dos = simulate(records, _policies(BASE_POLICIES), SimConfig(servers=2, **FIXED))
    assert [round(r["wait_us"]) for r in dos.rows] == [0, 0, 1000, 0]
    assert sorted(r["server"] for r in dos.rows[:2]) == [0, 1]


def test_des_ley_de_little_y_series():
    records = [_access(i * 0.0004) for i in range(500)]  # llegan cada 0.4 ms, servicio 1 ms
    result = simulate(records, _policies(BASE_POLICIES), SimConfig(servers=3, **FIXED))
    s = summarize(result)
    assert s["mean_in_system"] == pytest.approx(s["little_l"], rel=1e-6)
    assert s["utilization"] == pytest.approx(500 * 0.001 / (3 * s["horizon_s"]), rel=1e-6)

    series = time_series(result, bin_s=0.05)
    assert sum(b["arrivals"] for b in series) == sum(b["completions"] for b in series) == 500


def test_des_coincide_con_oraculo_y_marca_rezagadas():
    turno = policy("P_TURNO", events=[event("E_T")], reconfigure_on=["sesion_terminada"])
    fin_turno = {"t": 0.0015, "kind": "domain_event", "event": {"event_type": "sesion_terminada", "subject_id": "medico"}}
    records = [_access(0.0), _access(0.0), _access(0.0), fin_turno, _access(0.01)]
    result = simulate(records, _policies([turno]), SimConfig(servers=1, rules_module=RULES, **FIXED))

    # la tercera llego antes del evento pero se atendio despues
    assert [r["decision"] for r in result.rows] == ["allow", "allow", "deny", "deny"]
    assert [r["stale"] for r in result.rows] == [False, False, True, False]
    assert result.rows[2]["expected_arrival"] == "allow"
    assert all(r["correct"] for r in result.rows)


def test_des_baseline_decide_igual():
    records = [_access(0.0), _access(0.001, asset="nomina"), _access(0.002, subject="visitante"),
               {"t": 0.003, "kind": "policy_delete", "policy_id": "P_MEDICO"}, _access(0.004)]
    for engine in ("devac", "baseline"):
        result = simulate(records, _policies(BASE_POLICIES), SimConfig(engine=engine, **FIXED))
        assert [r["decision"] for r in result.rows] == ["allow", "deny", "deny", "deny"], engine
        assert all(r["correct"] for r in result.rows), engine


def test_des_registra_errores_de_control():
    records = [{"t": 0.0, "kind": "policy_delete", "policy_id": "NO_EXISTE"}, _access(0.001)]
    result = simulate(records, _policies(BASE_POLICIES), SimConfig(**FIXED))
    assert result.controls[0]["error"].startswith("PolicyNotFound")
    assert result.oracle_errors and result.rows[0]["correct"]


def test_intervalo_de_confianza():
    ci = confidence_interval([10.0, 12.0, 14.0])
    assert ci["mean"] == 12.0 and ci["sd"] == 2.0
    assert ci["ci95"] == pytest.approx(4.303 * 2 / 3 ** 0.5)
    assert confidence_interval([5.0])["ci95"] is None


# el evento cubierto queda fuera del indice; si cambia la politica que lo cubria, tiene que volver
def test_auditoria_no_debe_perder_eventos():
    copia = policy("P_COPIA", events=[event("E_COPIA", subject="enfermera", action="escribir", time="08:00-20:00")])
    cambio = {"t": 0.001, "kind": "policy_update",
              "policy": policy("P_ENFERMERA", effect="deny", events=[event("E_ENF", subject="enfermera", action="escribir")])}
    borra = {"t": 0.002, "kind": "policy_delete", "policy_id": "P_ENFERMERA"}
    records = [cambio, borra, _access(0.003, subject="enfermera", action="escribir")]
    result = simulate(records, _policies(BASE_POLICIES + [copia]), SimConfig(audit=True, **FIXED))
    assert result.rows[0]["decision"] == "allow"
