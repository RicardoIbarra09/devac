from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from devac.dpl import DPLSyntaxError, compile_dpl_to_policies
from devac_api.dependencies import get_engine
from devac_api.engine import DevacEngine
from devac_api.mapping import policy_from_dto, policy_summary, policy_to_dto
from devac_api.schemas import DplIn, PoliciesCreatedOut, PolicyIn, PolicyOut, PolicyPage, PolicySummary

router = APIRouter(prefix="/v1/policies", tags=["políticas"])


def _created(engine: DevacEngine, policies) -> PoliciesCreatedOut:
    communities = engine.community_map()
    return PoliciesCreatedOut(created=[policy_summary(p, communities.get(p.policy_id)) for p in policies])


@router.get("", response_model=PolicyPage)
async def list_policies(
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    engine: DevacEngine = Depends(get_engine),
) -> PolicyPage:
    communities = engine.community_map()
    policies = sorted(engine.evaluator.policies.values(), key=lambda p: p.policy_id)
    page = policies[offset:offset + limit]
    return PolicyPage(
        total=len(policies),
        offset=offset,
        limit=limit,
        items=[policy_summary(p, communities.get(p.policy_id)) for p in page],
    )


@router.get("/{policy_id}", response_model=PolicyOut)
async def get_policy(policy_id: str, engine: DevacEngine = Depends(get_engine)) -> PolicyOut:
    policy = engine.get_policy(policy_id)
    return policy_to_dto(policy, engine.community_map().get(policy_id))


@router.post("", response_model=PolicySummary, status_code=status.HTTP_201_CREATED)
async def create_policy(body: PolicyIn, engine: DevacEngine = Depends(get_engine)) -> PolicySummary:
    [policy] = await engine.add_policies([policy_from_dto(body)])
    return policy_summary(policy, engine.community_map().get(policy.policy_id))


@router.post("/batch", response_model=PoliciesCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_policies(body: List[PolicyIn], engine: DevacEngine = Depends(get_engine)) -> PoliciesCreatedOut:
    if not body:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "se requiere al menos una política")
    policies = await engine.add_policies([policy_from_dto(p) for p in body])
    return _created(engine, policies)


@router.post("/dpl", response_model=PoliciesCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_from_dpl(body: DplIn, engine: DevacEngine = Depends(get_engine)) -> PoliciesCreatedOut:
    try:
        compiled = compile_dpl_to_policies(body.source)
    except DPLSyntaxError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {"error": "dpl_syntax", "line": exc.line, "message": str(exc)})
    policies = await engine.add_policies(compiled)
    return _created(engine, policies)


@router.put("/{policy_id}", response_model=PolicySummary)
async def replace_policy(policy_id: str, body: PolicyIn, engine: DevacEngine = Depends(get_engine)) -> PolicySummary:
    if body.policy_id != policy_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "policy_id del cuerpo no coincide con la ruta")
    policy = await engine.update_policy(policy_from_dto(body))
    return policy_summary(policy, engine.community_map().get(policy_id))


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(policy_id: str, engine: DevacEngine = Depends(get_engine)) -> Response:
    await engine.delete_policy(policy_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
