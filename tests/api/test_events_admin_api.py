import time

from devac.reconfigurator import ReconfigurationLevel
from tests.api.conftest import BASE_POLICIES, build_engine, event, policy, request


def test_tipos_de_evento_de_infraestructura(make_client):
    client = make_client(BASE_POLICIES)
    assert len(client.get("/v1/events/types").json()) == 11


def test_evento_de_dominio_reconfigura_politica(make_client):
    politicas = [policy("P_TURNO", events=[event("E_TURNO")], reconfigure_on=["sesion_terminada"])]
    client = make_client(politicas, rules_module="devac_api.plugins.example_rules")
    assert client.post("/v1/evaluate", json=request()).json()["decision"] == "allow"

    r = client.post("/v1/events", json={"event_type": "sesion_terminada", "subject_id": "medico"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "processed"
    assert body["reconfigurations"] == [{"policy_id": "P_TURNO", "level": "PARAMETRICO", "cascade_depth": 0}]

    assert client.post("/v1/evaluate", json=request()).json()["decision"] == "deny"
    assert client.get("/v1/stats").json()["monitor"]["swap_count"] >= 1


def test_evento_tipado_sin_campos_obligatorios(make_client):
    client = make_client(BASE_POLICIES)
    assert client.post("/v1/events", json={"event_type": "sesion_terminada"}).status_code == 422
    assert client.post("/v1/events", json={"event_type": "Mal Nombre"}).status_code == 422


def test_evento_asincrono_responde_202_y_se_procesa_despues(make_client):
    politicas = [policy("P_TURNO", events=[event("E_TURNO")], reconfigure_on=["sesion_terminada"])]
    client = make_client(politicas, rules_module="devac_api.plugins.example_rules")

    # se encola, no espera
    r = client.post("/v1/events?wait=false", json={"event_type": "sesion_terminada", "subject_id": "medico"})
    assert r.status_code == 202 and r.json()["status"] == "accepted"

    deadline = time.monotonic() + 2  # esperar a que el evento se procese en segundo plano
    while client.get("/v1/stats").json()["reconfigurations_applied"] == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert client.post("/v1/evaluate", json=request()).json()["decision"] == "deny"


def test_alta_por_api_publica_evento_de_dominio(make_client):
    vigilante = policy("P_VIGILANTE", events=[event("E_VIG", subject="auditor")], reconfigure_on=["politica_agregada"])
    engine = build_engine([vigilante])

    def endurecer(p, _event):
        p.effect = "deny"
        return p

    # regla registrada directo en el engine
    engine.reconfigurator.register_rule("politica_agregada", endurecer, ReconfigurationLevel.CASCADA)
    client = make_client(engine=engine)

    client.post("/v1/policies", json=policy("P_OTRA", events=[event("E_OTRA", subject="otro")]))
    assert client.get("/v1/policies/P_VIGILANTE").json()["effect"] == "deny"


def test_estadisticas_reconstruccion_y_auditoria(make_client):
    client = make_client(BASE_POLICIES)
    stats = client.get("/v1/stats").json()
    assert stats["policies"] == len(BASE_POLICIES)
    assert stats["communities"] >= 1
    rebuilds_antes = stats["monitor"]["rebuilds"]

    r = client.post("/v1/admin/rebuild")
    assert r.status_code == 200
    assert r.json()["stats"]["monitor"]["rebuilds"] == rebuilds_antes + 1

    audit = client.get("/v1/admin/audit").json()
    assert {"redundant_events", "conflicts", "events_compared"} <= set(audit)


def test_auditoria_inexistente_y_rebuild_en_vacio(make_client):
    client = make_client(audit=False)
    assert client.get("/v1/admin/audit").status_code == 404
    assert client.post("/v1/admin/rebuild").status_code == 200
