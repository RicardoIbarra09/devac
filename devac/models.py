import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, field_validator


_TIME_RANGE_RE = re.compile(r"^\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*$")


def _parse_time_range(value: str) -> Optional[List[str]]:
    """
    Si `value` tiene forma "HH:MM-HH:MM" y ambas mitades son horas válidas,
    devuelve [inicio, fin]. En cualquier otro caso (categórico, formato
    inválido, etc.) devuelve None y el valor se deja como string tal cual.
    """
    match = _TIME_RANGE_RE.match(value)
    if not match:
        return None
    start_str, end_str = match.groups()
    try:
        datetime.strptime(start_str, "%H:%M")
        datetime.strptime(end_str, "%H:%M")
    except ValueError:
        return None
    return [start_str, end_str]


class AttributeComponent(BaseModel):
    """
    Componente de atributo en un evento: par (atributo, valor).

    El campo `value` acepta el DSL extendido de DEvAC:
      - str                         → valor exacto o "*" como comodín global
      - {"in": ["v1", "v2", ...]}   → pertenencia a lista
      - {"range": ["HH:MM","HH:MM"]}→ rango horario inclusivo

    Cualquier string con forma "HH:MM-HH:MM" se normaliza automáticamente
    a {"range": ["HH:MM","HH:MM"]} para que exista una única representación
    canónica de rango horario en todo el pipeline (grafo, índices, matcher).
    """
    attribute: str
    value: Union[str, Dict[str, Any]]

    @field_validator("value", mode="after")
    @classmethod
    def _normalize_time_range_string(cls, v):
        if isinstance(v, str):
            parsed = _parse_time_range(v)
            if parsed is not None:
                return {"range": parsed}
        return v


class Event(BaseModel):
    """
    Evento de política: conjunción completa de cinco dimensiones de acceso.
    """
    event_id: str
    subject:  AttributeComponent
    asset:    AttributeComponent
    space:    AttributeComponent
    time:     AttributeComponent
    action:   AttributeComponent


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
    """
    Política DEvAC: uno o más eventos de coincidencia con un efecto declarado.

    Campos extendidos:
      - reconfigure_on : tipos de evento de dominio que disparan reconfiguración
      - on_deny        : acción al denegar ("alert" | "escalate" | None)
    """
    policy_id:       str
    description:     str
    events:          List[Event]
    effect:          str
    reconfigure_on:  List[str]     = []
    on_deny:         Optional[str] = None

    @classmethod
    def from_json(cls, path: str) -> "Policy":
        with open(path, "r", encoding="utf-8") as f:
            json_data = f.read()
        return cls.model_validate_json(json_data)
