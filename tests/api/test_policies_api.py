from tests.api.conftest import BASE_POLICIES, event, policy, request


def test_alta_consulta_y_listado(make_client):
    client = make_client(BASE_POLICIES)
    nueva = policy("P_FARMACIA", events=[event("E_FAR", subject="farmaceutico", asset="receta")])

    r = client.post("/v1/policies", json=nueva)
    assert r.status_code == 201
    assert r.json()["community"] is not None

    detalle = client.get("/v1/policies/P_FARMACIA").json()
    assert detalle["events"][0]["subject"] == "farmaceutico"

    pagina = client.get("/v1/policies?offset=0&limit=2").json()
    assert pagina["total"] == len(BASE_POLICIES) + 1
    assert len(pagina["items"]) == 2

    decision = client.post("/v1/evaluate", json=request(subject="farmaceutico", asset="receta")).json()
    assert decision["policy_id"] == "P_FARMACIA"


def test_alta_acepta_valores_planos_y_dsl(make_client):
    client = make_client(BASE_POLICIES)
    plana = {
        "policy_id": "P_PLANA", "effect": "allow",
        "events": [{"event_id": "E_PLANA", "subject": {"in": ["residente", "interno"]}, "asset": "laboratorio",
                    "space": "*", "time": {"range": ["07:00", "15:00"]}, "action": "leer"}],
    }
    assert client.post("/v1/policies", json=plana).status_code == 201
    r = client.post("/v1/evaluate", json=request(subject="interno", asset="laboratorio", space="piso2", time="09:00"))
    assert r.json()["decision"] == "allow"


def test_rechaza_duplicados(make_client):
    client = make_client(BASE_POLICIES)
    assert client.post("/v1/policies", json=policy("P_MEDICO")).status_code == 409
    otra_con_evento_repetido = policy("P_NUEVA", events=[event("E_MED")])
    assert client.post("/v1/policies", json=otra_con_evento_repetido).status_code == 409


def test_actualizacion_cambia_la_decision(make_client):
    client = make_client(BASE_POLICIES)
    actualizada = policy("P_MEDICO", effect="deny", events=[event("E_MED", time={"range": ["08:00", "20:00"]})])

    assert client.put("/v1/policies/P_MEDICO", json=actualizada).status_code == 200
    for engine in ("devac", "baseline"):
        assert client.post(f"/v1/evaluate?engine={engine}", json=request()).json()["decision"] == "deny"

    assert client.put("/v1/policies/OTRA", json=actualizada).status_code == 422
    assert client.put("/v1/policies/NO_EXISTE", json=policy("NO_EXISTE")).status_code == 404


def test_baja(make_client):
    client = make_client(BASE_POLICIES)
    assert client.delete("/v1/policies/P_MEDICO").status_code == 204
    assert client.get("/v1/policies/P_MEDICO").status_code == 404
    assert client.post("/v1/evaluate", json=request()).json()["decision"] == "deny"
    assert client.delete("/v1/policies/P_MEDICO").status_code == 404


def test_servicio_vacio_acepta_su_primera_politica(make_client):
    client = make_client()
    assert client.post("/v1/evaluate", json=request()).json()["decision"] == "deny"
    assert client.post("/v1/policies", json=policy("P_PRIMERA")).json()["community"] == "0"
    assert client.post("/v1/evaluate", json=request()).json()["policy_id"] == "P_PRIMERA"


def test_dpl_con_error_de_sintaxis_indica_la_linea(make_client):
    client = make_client()
    r = client.post("/v1/policies/dpl", json={"source": "POLICY P1 EFFECT ALLOW {\n  EVENT E1 WHERE SUBJECT = x;\n}"})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "dpl_syntax"
    assert r.json()["detail"]["line"] == 2


def test_valida_dsl(make_client):
    client = make_client(BASE_POLICIES)
    mala = policy("P_MALA", events=[event("E_MALA", time={"range": ["08:00"]})])
    assert client.post("/v1/policies", json=mala).status_code == 422
    sin_eventos = {**policy("P_VACIA"), "events": []}
    assert client.post("/v1/policies", json=sin_eventos).status_code == 422


def test_activar_y_desactivar_sin_reenviar(make_client):
    client = make_client(BASE_POLICIES)
    version = int(client.get("/v1/policies").headers.get("X-Policy-Version", 0))

    r = client.post("/v1/policies/P_MEDICO/deactivate")
    assert r.status_code == 200
    assert r.json()["active"] is False and r.json()["community"] is None
    assert int(r.headers["X-Policy-Version"]) > version
    # conserva sus eventos, pero ya no decide
    detalle = client.get("/v1/policies/P_MEDICO").json()
    assert detalle["active"] is False and [e["event_id"] for e in detalle["events"]] == ["E_MED"]
    for engine in ("devac", "baseline"):
        assert client.post(f"/v1/evaluate?engine={engine}", json=request()).json()["decision"] == "deny"
    assert client.get("/v1/stats").json()["active_policies"] == len(BASE_POLICIES) - 1

    # un PUT sin "active" no la reactiva
    actualizada = policy("P_MEDICO", events=[event("E_MED", time={"range": ["07:00", "20:00"]})])
    assert client.put("/v1/policies/P_MEDICO", json=actualizada).json()["active"] is False

    r = client.post("/v1/policies/P_MEDICO/activate")
    assert r.status_code == 200 and r.json()["active"] is True and r.json()["community"] is not None
    for engine in ("devac", "baseline"):
        decision = client.post(f"/v1/evaluate?engine={engine}", json=request(time="07:30")).json()
        assert decision["decision"] == "allow" and decision["policy_id"] == "P_MEDICO"

    assert client.post("/v1/policies/NO_EXISTE/activate").status_code == 404
