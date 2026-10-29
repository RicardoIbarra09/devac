from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

DslValue = Union[str, Dict[str, Any]]  # string, '*', {'in': [...]} o {'range': [ini, fin]}


class Engine(str, Enum):
    devac = "devac"
    baseline = "baseline"


class AccessRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # campos de mas = 422

    subject: str = Field(..., min_length=1, examples=["medico"])
    asset: str = Field(..., min_length=1, examples=["expediente"])
    space: str = Field(..., min_length=1, examples=["urgencias"])
    time: str = Field(..., min_length=1, examples=["10:30"])
    action: str = Field(..., min_length=1, examples=["leer"])


class DecisionOut(BaseModel):
    decision: Literal["allow", "deny"]
    policy_id: Optional[str]
    event_id: Optional[str]
    steps: int
    eval_time_us: float  # solo el tiempo de evaluate()
    engine: Engine
    policy_version: int  # version de politicas con la que se evaluo
    consistent: bool  # false si se evaluo con una escritura a medias


class BatchIn(BaseModel):
    requests: List[AccessRequestIn] = Field(..., min_length=1)


class BatchOut(BaseModel):
    engine: Engine
    results: List[DecisionOut]
    total_steps: int
    total_eval_time_us: float


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(..., min_length=1)
    subject: DslValue
    asset: DslValue
    space: DslValue
    time: DslValue
    action: DslValue

    @field_validator("subject", "asset", "space", "time", "action", mode="before")
    @classmethod
    def _unwrap_and_check(cls, v):
        # tambien acepta el formato del nucleo {attribute, value}
        if isinstance(v, dict) and "value" in v:
            v = v["value"]
        if isinstance(v, str):
            if not v.strip():
                raise ValueError("el valor no puede estar vacío")
            return v
        if isinstance(v, dict):
            if set(v) == {"in"} and isinstance(v["in"], list) and v["in"]:
                return v
            if set(v) == {"range"} and isinstance(v["range"], list) and len(v["range"]) == 2:
                return v
        raise ValueError('valor inválido: se esperaba string, {"in": [...]} o {"range": [inicio, fin]}')


class PolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str = Field(..., min_length=1)
    description: str = ""
    events: List[EventIn] = Field(..., min_length=1)  # una politica sin eventos no tiene sentido
    effect: Literal["allow", "deny"]
    reconfigure_on: List[str] = []
    on_deny: Optional[Literal["alert", "escalate"]] = None
    # al crear, None = activa; en PUT, None = conserva el estado actual
    active: Optional[bool] = None


class EventOut(BaseModel):
    event_id: str
    subject: DslValue
    asset: DslValue
    space: DslValue
    time: DslValue
    action: DslValue


class PolicyOut(BaseModel):
    policy_id: str
    description: str
    effect: str
    events: List[EventOut]
    reconfigure_on: List[str]
    on_deny: Optional[str]
    active: bool
    community: Optional[str]


class PolicySummary(BaseModel):
    policy_id: str
    effect: str
    n_events: int
    active: bool
    community: Optional[str]


class PolicyPage(BaseModel):
    total: int
    offset: int
    limit: int
    items: List[PolicySummary]


class DplIn(BaseModel):
    source: str = Field(..., min_length=1, description="Texto DPL con una o más políticas")


class PoliciesCreatedOut(BaseModel):
    created: List[PolicySummary]


class DomainEventIn(BaseModel):
    model_config = ConfigDict(extra="allow")  # los campos propios de cada evento van al mismo nivel

    event_type: str = Field(..., pattern=r"^[a-z][a-z0-9_]*$")  # snake_case
    source: str = "api"
    payload: Dict[str, Any] = {}


class ReconfigurationOut(BaseModel):
    policy_id: str
    level: str
    cascade_depth: int


class DomainEventOut(BaseModel):
    event_id: str
    event_type: str
    status: Literal["processed", "accepted"]
    reconfigurations: List[ReconfigurationOut]


class StatsOut(BaseModel):
    policies: int
    active_policies: int
    communities: int
    events: int
    reconfigurations_applied: int
    policy_version: int
    monitor: Dict[str, Any]
    last_audit: Optional[str]


class RebuildOut(BaseModel):
    rebuilt: bool
    duration_ms: float
    stats: StatsOut
