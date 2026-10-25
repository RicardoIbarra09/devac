import time
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, status

from devac_api.dependencies import get_engine
from devac_api.engine import DevacEngine
from devac_api.schemas import RebuildOut, StatsOut

router = APIRouter(tags=["administración"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/v1/stats", response_model=StatsOut)
async def stats(engine: DevacEngine = Depends(get_engine)) -> StatsOut:
    return StatsOut(**engine.stats())


# fuerza la reconstruccion de nivel 4
@router.post("/v1/admin/rebuild", response_model=RebuildOut)
async def rebuild(engine: DevacEngine = Depends(get_engine)) -> RebuildOut:
    start = time.perf_counter()
    await engine.rebuild()
    return RebuildOut(
        rebuilt=True,
        duration_ms=(time.perf_counter() - start) * 1e3,
        stats=StatsOut(**engine.stats()),
    )


@router.get("/v1/admin/audit")
async def last_audit(engine: DevacEngine = Depends(get_engine)) -> dict:
    report = engine.monitor.last_audit_report
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "todavía no se ha ejecutado ninguna auditoría")
    return asdict(report)
