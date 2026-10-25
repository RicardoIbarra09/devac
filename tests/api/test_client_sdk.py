import asyncio

import httpx
import pytest

from devac_api.main import create_app
from devac_client import DevacAPIError, DevacClient
from devac_client.loadgen import build_schedule, run_load, summarize
from tests.api.conftest import BASE_POLICIES, build_engine, event, policy, request


# el sdk habla con la app en memoria, sin levantar servidor
def _transport():
    return httpx.ASGITransport(app=create_app(engine=build_engine(BASE_POLICIES)))


def test_sdk_recorre_el_flujo_completo():
    async def flujo():
        async with DevacClient("http://devac", transport=_transport()) as c:
            assert (await c.health())["status"] == "ok"
            await c.add_policy(policy("P_SDK", events=[event("E_SDK", subject="tecnico", asset="equipo")]))
            decision, server_us = await c.evaluate_timed(request(subject="tecnico", asset="equipo"))
            assert decision["policy_id"] == "P_SDK" and server_us > 0
            await c.delete_policy("P_SDK")
            assert (await c.evaluate(request(subject="tecnico", asset="equipo")))["decision"] == "deny"
            with pytest.raises(DevacAPIError) as err:
                await c.get_policy("P_SDK")
            assert err.value.status_code == 404

    asyncio.run(flujo())


def test_generador_de_carga_lazo_cerrado_y_abierto():
    items = [(None, request()), (None, request(subject="visitante"))]

    cerrado = build_schedule(items, total=40, rate=0, speed=0)
    samples, duracion = asyncio.run(run_load("http://devac", cerrado, concurrency=8, warmup=4, transport=_transport()))
    resumen = summarize(samples, duracion)
    assert resumen["ok"] == 40 and resumen["errors"] == 0
    assert resumen["decisions"] == {"allow": 20, "deny": 20}

    abierto = build_schedule(items, total=20, rate=400, speed=0)
    samples, duracion = asyncio.run(run_load("http://devac", abierto, concurrency=4, transport=_transport()))
    assert summarize(samples, duracion)["ok"] == 20
    assert duracion >= 19 / 400  # a 400/s, 20 peticiones tardan al menos 19/400 s


def test_replay_de_traza_respeta_tiempos():
    items = [(0.0, request()), (0.5, request()), (1.0, request())]
    schedule = build_schedule(items, total=6, rate=0, speed=10)  # traza a 10x
    assert [round(t, 3) for t, _ in schedule] == [0.0, 0.05, 0.1, 0.15, 0.2, 0.25]
