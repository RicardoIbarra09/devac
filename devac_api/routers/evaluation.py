from fastapi import APIRouter, Depends, HTTPException, Query, status

from devac_api.dependencies import get_engine
from devac_api.engine import DevacEngine
from devac_api.mapping import request_from_dto
from devac_api.schemas import AccessRequestIn, BatchIn, BatchOut, DecisionOut, Engine

router = APIRouter(prefix="/v1", tags=["evaluación"])

# ?engine=devac o ?engine=baseline
EngineQuery = Query(Engine.devac, alias="engine", description="devac (ruteo por comunidades) o baseline (escaneo lineal)")


def _decision(raw: dict, engine: Engine, version: int, consistent: bool) -> DecisionOut:
    return DecisionOut(
        decision=raw["result"],
        policy_id=raw["policy_id"],
        event_id=raw["event_id"],
        steps=raw["steps"],
        eval_time_us=raw["eval_time"] * 1e6,  # segundos -> microsegundos
        engine=engine,
        policy_version=version,
        consistent=consistent,
    )


# se lee antes de evaluar; evaluate es sincrono, nada se mete en medio
def _snapshot(engine: DevacEngine) -> tuple:
    return engine.policy_version, not engine.write_in_progress


# async a proposito: evaluate es cpu corto, un threadpool solo agrega overhead
@router.post("/evaluate", response_model=DecisionOut)
async def evaluate(
    body: AccessRequestIn,
    engine_name: Engine = EngineQuery,
    engine: DevacEngine = Depends(get_engine),
) -> DecisionOut:
    version, consistent = _snapshot(engine)
    return _decision(engine.evaluate(request_from_dto(body), engine_name), engine_name, version, consistent)


@router.post("/evaluate/batch", response_model=BatchOut)
async def evaluate_batch(
    body: BatchIn,
    engine_name: Engine = EngineQuery,
    engine: DevacEngine = Depends(get_engine),
) -> BatchOut:
    limit = engine.settings.max_batch_size
    if len(body.requests) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"máximo {limit} solicitudes por lote")
    version, consistent = _snapshot(engine)
    results = [
        _decision(engine.evaluate(request_from_dto(r), engine_name), engine_name, version, consistent)
        for r in body.requests
    ]
    return BatchOut(
        engine=engine_name,
        results=results,
        total_steps=sum(r.steps for r in results),
        total_eval_time_us=sum(r.eval_time_us for r in results),
    )
