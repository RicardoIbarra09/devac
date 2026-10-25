from typing import Iterable, Optional

import pytest
from fastapi.testclient import TestClient

from devac.models import Policy
from devac_api.config import Settings
from devac_api.engine import DevacEngine
from devac_api.main import create_app

# etiquetas en espanol, como en los datasets
LABELS = {"subject": "rol", "asset": "recurso", "space": "ubicacion", "time": "rango_horario", "action": "accion"}


def event(eid, subject="medico", asset="expediente", space="urgencias", time="*", action="leer") -> dict:
    values = {"subject": subject, "asset": asset, "space": space, "time": time, "action": action}
    return {"event_id": eid, **{d: {"attribute": LABELS[d], "value": v} for d, v in values.items()}}


def policy(pid, effect="allow", events=None, **extra) -> dict:
    return {"policy_id": pid, "description": pid, "effect": effect, "events": events or [event(f"{pid}_e1")], **extra}


def request(subject="medico", asset="expediente", space="urgencias", time="10:30", action="leer") -> dict:
    return {"subject": subject, "asset": asset, "space": space, "time": time, "action": action}


def build_engine(policies: Iterable[dict] = (), **settings) -> DevacEngine:
    return DevacEngine(Settings(**settings), [Policy.model_validate(p) for p in policies])


# crea clientes con el engine ya construido y los cierra al final
@pytest.fixture
def make_client():
    clients = []

    def _make(policies: Iterable[dict] = (), engine: Optional[DevacEngine] = None, **settings) -> TestClient:
        engine = engine or build_engine(policies, **settings)
        client = TestClient(create_app(engine=engine))
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


BASE_POLICIES = [
    policy("P_MEDICO", events=[event("E_MED", time={"range": ["08:00", "20:00"]})]),
    policy("P_ENFERMERA", events=[event("E_ENF", subject="enfermera", action="escribir")]),
    # rango que cruza medianoche
    policy("P_NOCHE", events=[event("E_NOCHE", subject="guardia", asset="*", space="*", time="22:00-06:00")]),
    policy("P_BLOQUEO", effect="deny", events=[event("E_BLOQ", subject="medico", asset="nomina", space="*")]),
]
