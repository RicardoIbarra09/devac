import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, field_validator


# detecta strings tipo HH:MM-HH:MM
_TIME_RANGE_RE = re.compile(r"^\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*$")


def _parse_time_range(value: str) -> Optional[List[str]]:
    match = _TIME_RANGE_RE.match(value)
    if not match:
        return None
    start_str, end_str = match.groups()
    try:
        # valida que las dos horas existan
        datetime.strptime(start_str, "%H:%M")
        datetime.strptime(end_str, "%H:%M")
    except ValueError:
        return None
    return [start_str, end_str]


class AttributeComponent(BaseModel):
    attribute: str
    value: Union[str, Dict[str, Any]]  # string, '*', {'in': [...]} o {'range': [ini, fin]}

    @field_validator("value", mode="after")
    @classmethod
    def _normalize_time_range_string(cls, v):
        if isinstance(v, str):
            parsed = _parse_time_range(v)
            if parsed is not None:
                return {"range": parsed}  # un rango en texto se guarda siempre como dict
        return v


# un evento = las 5 dimensiones juntas
class Event(BaseModel):
    event_id: str
    subject:  AttributeComponent
    asset:    AttributeComponent
    space:    AttributeComponent
    time:     AttributeComponent
    action:   AttributeComponent


# peticion concreta, mismos campos que Event
class AccessRequest(BaseModel):
    subject: AttributeComponent
    asset:   AttributeComponent
    space:   AttributeComponent
    time:    AttributeComponent
    action:  AttributeComponent

    @classmethod
    def from_json(cls, path: str) -> "AccessRequest":
        with open(path, "r", encoding="utf-8") as f:
            json_data = f.read()
        return cls.model_validate_json(json_data)


class Policy(BaseModel):
    policy_id:       str
    description:     str
    events:          List[Event]
    effect:          str
    reconfigure_on:  List[str]     = []  # eventos de dominio que pueden reescribir la politica
    on_deny:         Optional[str] = None  # alert / escalate

    @classmethod
    def from_json(cls, path: str) -> "Policy":
        with open(path, "r", encoding="utf-8") as f:
            json_data = f.read()
        return cls.model_validate_json(json_data)
