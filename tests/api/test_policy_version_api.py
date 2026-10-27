import asyncio

import httpx

from devac_api.main import create_app
from tests.api.conftest import BASE_POLICIES, build_engine, event, policy, request


def test_evaluate_reporta_version_y_las_escrituras_la_suben(make_client):
    client = make_client(BASE_POLICIES)
    body = client.post("/v1/evaluate", json=request()).json()
    assert body["policy_version"] == 0 and body["consistent"] is True

    r = client.post("/v1/policies", json=policy("P_NUEVA", events=[event("E_NUEVA", subject="tecnico")]))
    assert r.headers["x-policy-version"] == "1"
    r = client.put("/v1/policies/P_NUEVA", json=policy("P_NUEVA", effect="deny", events=[event("E_NUEVA", subject="tecnico")]))
    assert r.headers["x-policy-version"] == "2"
    r = client.delete("/v1/policies/P_NUEVA")
    assert r.status_code == 204 and r.headers["x-policy-version"] == "3"
    r = client.post("/v1/events", json={"event_type": "sesion_terminada", "subject_id": "medico"})
    assert r.headers["x-policy-version"] == "4"

    assert client.delete("/v1/policies/NO_EXISTE").status_code == 404  # lo que falla no cuenta
    assert client.get("/v1/stats").json()["policy_version"] == 4
    batch = client.post("/v1/evaluate/batch", json={"requests": [request(), request()]}).json()
    assert {r["policy_version"] for r in batch["results"]} == {4}


def test_evaluacion_durante_una_escritura_no_es_consistente():
    engine = build_engine(BASE_POLICIES)
    seen = []

    # suscriptor que mira el estado a la mitad de la escritura
    async def espia(_event):
        seen.append((engine.policy_version, engine.write_in_progress))

    engine.bus.subscribe("politica_agregada", espia)
    transport = httpx.ASGITransport(app=create_app(engine=engine))

    async def flujo():
        async with httpx.AsyncClient(transport=transport, base_url="http://devac") as http:
            await http.post("/v1/policies", json=policy("P_X", events=[event("E_X", subject="otro")]))

    asyncio.run(flujo())
    assert seen == [(0, True)]
    assert engine.policy_version == 1 and not engine.write_in_progress
