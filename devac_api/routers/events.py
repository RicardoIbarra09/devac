from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import ValidationError

from devac.events import INFRASTRUCTURE_EVENTS, DomainEvent
from devac_api.dependencies import get_engine
from devac_api.engine import DevacEngine
from devac_api.schemas import DomainEventIn, DomainEventOut, ReconfigurationOut

router = APIRouter(prefix="/v1/events", tags=["eventos de dominio"])


@router.get("/types", response_model=list[str])
async def infrastructure_event_types() -> list[str]:
    return sorted(INFRASTRUCTURE_EVENTS)


@router.post("", response_model=DomainEventOut)
async def publish_event(
    body: DomainEventIn,
    response: Response,
    wait: bool = Query(True, description="false: se encola y responde 202 sin esperar la reconfiguración"),
    engine: DevacEngine = Depends(get_engine),
) -> DomainEventOut:
    # si es de los 11 se valida con su clase
    event_cls = INFRASTRUCTURE_EVENTS.get(body.event_type, DomainEvent)
    try:
        event = event_cls.model_validate(body.model_dump())
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.errors(include_url=False, include_context=False))

    applied = await engine.publish_event(event, wait=wait)
    # wait=false
    if applied is None:
        response.status_code = status.HTTP_202_ACCEPTED
        return DomainEventOut(event_id=event.event_id, event_type=event.event_type, status="accepted", reconfigurations=[])

    return DomainEventOut(
        event_id=event.event_id,
        event_type=event.event_type,
        status="processed",
        reconfigurations=[
            ReconfigurationOut(policy_id=r["policy_id"], level=r["level"].name, cascade_depth=r["cascade_depth"])
            for r in applied
        ],
    )
