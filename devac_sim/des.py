import asyncio
import contextlib
import gc
import heapq
import io
import time
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional

from devac.baseline.evaluator import TraditionalPolicyEvaluator
from devac.models import Policy

from devac_api.config import Settings
from devac_api.engine import DevacEngine, EngineError
from devac_api.mapping import request_from_dto
from devac_api.schemas import AccessRequestIn

from devac_sim.oracle import Oracle, load_rules
from devac_sim.trace import ACCESS, DOMAIN_EVENT, POLICY_ADD, POLICY_DELETE, POLICY_UPDATE, build_domain_event


@dataclass
class SimConfig:
    servers: int = 1
    engine: str = "devac"  # devac o baseline
    service_scale: float = 1.0  # multiplica el tiempo medido de evaluate()
    overhead_us: float = 0.0  # costo fijo extra por peticion (http, serializacion)
    rules_module: Optional[str] = None
    similarity_threshold: float = 0.3
    stress_threshold: float = 0.5
    audit: bool = True


@dataclass
class SimResult:
    rows: List[dict]
    controls: List[dict]
    servers: int
    rebuilds: int
    oracle_errors: List[str] = field(default_factory=list)


def _build_engine(policies: List[Policy], config: SimConfig) -> DevacEngine:
    settings = Settings(
        similarity_threshold=config.similarity_threshold,
        stress_threshold=config.stress_threshold,
        audit=config.audit,
        rules_module=config.rules_module,
    )
    with contextlib.redirect_stdout(io.StringIO()):
        engine = DevacEngine(settings, policies)
    # la reconstruccion nivel 4 corre en el mismo hilo, si no la corrida no es reproducible
    engine.monitor._trigger_rebuild = engine.monitor.rebuild_now
    return engine


async def _apply_control(engine: DevacEngine, record: dict) -> Optional[str]:
    kind = record["kind"]
    try:
        # el nucleo imprime cada hot-swap
        with contextlib.redirect_stdout(io.StringIO()):
            if kind == POLICY_ADD:
                await engine.add_policies([Policy.model_validate(record["policy"])])
            elif kind == POLICY_UPDATE:
                await engine.update_policy(Policy.model_validate(record["policy"]))
            elif kind == POLICY_DELETE:
                await engine.delete_policy(record["policy_id"])
            elif kind == DOMAIN_EVENT:
                await engine.publish_event(build_domain_event(record["event"]))
    except EngineError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


class _Server:
    def __init__(self, engine: DevacEngine, config: SimConfig):
        self.engine = engine
        self.config = config
        self.baseline: Optional[TraditionalPolicyEvaluator] = None
        self.refresh()

    # el baseline se arma fuera del tiempo medido
    def refresh(self) -> None:
        if self.config.engine == "baseline":
            self.baseline = TraditionalPolicyEvaluator(list(self.engine.evaluator.policies.values()))

    def evaluate(self, request):
        evaluator = self.baseline if self.baseline is not None else self.engine.evaluator
        t0 = time.perf_counter_ns()
        result = evaluator.evaluate(request)
        return result, (time.perf_counter_ns() - t0) / 1e3


async def _simulate(records: List[dict], policies: List[Policy], config: SimConfig, oracle: Oracle) -> SimResult:
    engine = _build_engine(policies, config)
    server = _Server(engine, config)
    scale, overhead_s = config.service_scale, config.overhead_us / 1e6

    rows: List[dict] = []
    controls: List[dict] = []
    free = list(range(config.servers))  # servidores libres, se toma siempre el de menor id
    busy: List[tuple] = []  # (t_fin, servidor)
    queue: deque = deque()  # FIFO de indices de rows
    version = 0

    def start(row_index: int, t_start: float, server_id: int) -> None:
        row = rows[row_index]
        result, eval_us = server.evaluate(row.pop("_request"))
        service = eval_us / 1e6 * scale + overhead_s
        row.update(
            t_start=t_start, t_end=t_start + service, server=server_id,
            service_us=service * 1e6, eval_us=eval_us, decision=result["result"],
            policy_id=result["policy_id"], steps=result.get("steps"), version_served=version,
        )
        heapq.heappush(busy, (row["t_end"], server_id))

    # empate con un registro de la traza: primero salen los que terminan
    def release_until(t: float) -> None:
        while busy and busy[0][0] <= t:
            t_end, server_id = heapq.heappop(busy)
            if queue:
                start(queue.popleft(), t_end, server_id)
            else:
                heapq.heappush(free, server_id)

    for record in records:
        t = record["t"]
        release_until(t)
        if record["kind"] == ACCESS:
            request = request_from_dto(AccessRequestIn(**record["request"]))
            rows.append({
                "index": len(rows), "t_arrival": t, "version_arrival": version,
                "request": record["request"], "_request": request,
            })
            if free:
                start(len(rows) - 1, t, heapq.heappop(free))
            else:
                queue.append(len(rows) - 1)
            continue

        # el plano de control se aplica al instante en tiempo virtual
        t0 = time.perf_counter_ns()
        error = await _apply_control(engine, record)
        cost_us = (time.perf_counter_ns() - t0) / 1e3
        version = oracle.apply(record)
        server.refresh()
        controls.append({"t": t, "kind": record["kind"], "version": version, "real_cost_us": cost_us, "error": error})
        gc.collect(1)  # lo que dejo la reconstruccion no debe caer dentro de una medicion

    release_until(float("inf"))
    return SimResult(rows=rows, controls=controls, servers=config.servers,
                     rebuilds=engine.monitor.get_stats()["rebuilds"], oracle_errors=list(oracle.errors))


def _label(result: SimResult, oracle: Oracle) -> None:
    for row in result.rows:
        request = row.pop("request")
        row["expected_arrival"], row["covered"] = oracle.decide(request, row["version_arrival"])
        expected_served, _ = oracle.decide(request, row["version_served"])
        row["correct"] = row["decision"] == expected_served  # contra lo que valia al atenderla
        row["stale"] = row["version_served"] != row["version_arrival"]
        row["wait_us"] = (row["t_start"] - row["t_arrival"]) * 1e6
        row["response_us"] = (row["t_end"] - row["t_arrival"]) * 1e6


def simulate(records: List[dict], policies: List[Policy], config: SimConfig) -> SimResult:
    if config.servers < 1:
        raise ValueError("se necesita al menos un servidor")
    oracle = Oracle(policies, load_rules(config.rules_module))
    gc.collect()
    gc.disable()  # sin pausas del gc dentro de evaluate()
    try:
        result = asyncio.run(_simulate(records, policies, config, oracle))
    finally:
        gc.enable()
    _label(result, oracle)
    return result
