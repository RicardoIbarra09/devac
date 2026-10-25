import json
from pathlib import Path

from tests.api.conftest import BASE_POLICIES, event, policy, request

ROOT = Path(__file__).resolve().parents[2]


def test_health_y_cabecera_de_tiempo(make_client):
    client = make_client(BASE_POLICIES)
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert float(r.headers["x-process-time-us"]) >= 0


def test_permite_cuando_hay_politica_allow(make_client):
    client = make_client(BASE_POLICIES)
    body = client.post("/v1/evaluate", json=request()).json()
    assert body["decision"] == "allow"
    assert body["policy_id"] == "P_MEDICO"
    assert body["engine"] == "devac"
    assert body["steps"] >= 1


def test_deniega_por_defecto_sin_cobertura(make_client):
    client = make_client(BASE_POLICIES)
    body = client.post("/v1/evaluate", json=request(subject="visitante")).json()
    assert body == {**body, "decision": "deny", "policy_id": None, "event_id": None, "steps": 0}


def test_rango_que_cruza_medianoche(make_client):
    client = make_client(BASE_POLICIES)
    assert client.post("/v1/evaluate", json=request(subject="guardia", time="02:00")).json()["decision"] == "allow"
    assert client.post("/v1/evaluate", json=request(subject="guardia", time="12:00")).json()["decision"] == "deny"


def test_deny_gana_en_ambos_motores(make_client):
    conflict = [
        policy("P_ALLOW", events=[event("E_A", asset="nomina")]),
        policy("P_DENY", effect="deny", events=[event("E_D", asset="nomina")]),
    ]
    client = make_client(conflict)
    for engine in ("devac", "baseline"):
        body = client.post(f"/v1/evaluate?engine={engine}", json=request(asset="nomina")).json()
        assert body["decision"] == "deny", engine
        assert body["policy_id"] == "P_DENY", engine


# las 200 peticiones de nimbuslab
def test_devac_y_baseline_coinciden_en_dataset_real(make_client):
    policies = json.loads((ROOT / "experiments/nimbuslab_policies.json").read_text())
    requests = json.loads((ROOT / "experiments/nimbuslab_requests.json").read_text())
    flat = [{d: str(r[d]["value"]) for d in ("subject", "asset", "space", "time", "action")} for r in requests]
    client = make_client(policies)

    devac = client.post("/v1/evaluate/batch?engine=devac", json={"requests": flat}).json()
    lineal = client.post("/v1/evaluate/batch?engine=baseline", json={"requests": flat}).json()

    assert [r["decision"] for r in devac["results"]] == [r["decision"] for r in lineal["results"]]
    assert devac["total_steps"] < lineal["total_steps"]  # y con menos comparaciones


def test_politicas_dpl_hacen_match_con_solicitudes_planas(make_client):
    client = make_client()
    dpl = """
    POLICY P_DPL EFFECT ALLOW {
        EVENT E_DPL WHERE (SUBJECT = medico, RESOURCE = expediente, LOCATION = urgencias, TIME = *, ACTION = leer);
    }
    """
    assert client.post("/v1/policies/dpl", json={"source": dpl}).status_code == 201
    assert client.post("/v1/evaluate", json=request()).json()["policy_id"] == "P_DPL"


def test_lote_suma_pasos_y_respeta_limite(make_client):
    client = make_client(BASE_POLICIES, max_batch_size=2)  # limite chico para probar el 413
    body = client.post("/v1/evaluate/batch", json={"requests": [request(), request(subject="visitante")]}).json()
    assert [r["decision"] for r in body["results"]] == ["allow", "deny"]
    assert body["total_steps"] == sum(r["steps"] for r in body["results"])

    r = client.post("/v1/evaluate/batch", json={"requests": [request()] * 3})
    assert r.status_code == 413


def test_valida_entrada(make_client):
    client = make_client(BASE_POLICIES)
    incompleta = {k: v for k, v in request().items() if k != "action"}
    assert client.post("/v1/evaluate", json=incompleta).status_code == 422
    assert client.post("/v1/evaluate", json={**request(), "extra": "x"}).status_code == 422
    assert client.post("/v1/evaluate?engine=otro", json=request()).status_code == 422
