from functools import lru_cache
from typing import Union, Dict, Any, List, Optional, Tuple
from devac.models import Event, AttributeComponent, AccessRequest


# HH:MM -> minutos del dia; None si no es una hora valida
@lru_cache(maxsize=4096)
def _minutes(text: str) -> Optional[int]:
    hh, sep, mm = text.strip().partition(":")
    if not sep or not (hh + mm).isascii() or not (hh.isdigit() and mm.isdigit()) or len(hh) > 2 or len(mm) > 2:
        return None
    h, m = int(hh), int(mm)
    if h > 23 or m > 59:
        return None
    return h * 60 + m


@lru_cache(maxsize=1024)
def _range_minutes(policy_range: str) -> Optional[Tuple[int, int]]:
    start, sep, end = policy_range.partition("-")
    if not sep:
        return None
    start_min, end_min = _minutes(start), _minutes(end)
    if start_min is None or end_min is None:
        return None
    return start_min, end_min


class EventMatcher:
    @staticmethod
    def is_time_in_range(policy_range: str, request_time: str) -> bool:
        bounds = _range_minutes(policy_range)
        point = _minutes(request_time)
        if bounds is None or point is None:
            return False
        start, end = bounds

        # rango normal
        if start <= end:
            return start <= point <= end
        return point >= start or point <= end  # rango que cruza medianoche (ej 23:00-06:00)

    # claves con las que se indexa un valor
    @staticmethod
    def expand_value(value: Union[str, dict], attr: str) -> List[str]:
        if isinstance(value, str):
            return [value.strip().lower()]
        if isinstance(value, dict):
            if "in" in value:
                return [str(v).strip().lower() for v in value["in"]]
            if "range" in value:
                return []
        return []

    @staticmethod
    def _match_value(val_event: Union[str, dict], val_req: str, attr: str) -> bool:
        if isinstance(val_event, str):
            # comodin
            if val_event == "*":
                return True
            if attr == "rango_horario" and "-" in val_event:
                return EventMatcher.is_time_in_range(val_event, val_req)
            return val_event == val_req

        if isinstance(val_event, dict):
            if "in" in val_event:
                # lista de valores permitidos
                allowed = [str(v).strip().lower() for v in val_event["in"]]
                return val_req in allowed
            if "range" in val_event:
                bounds = val_event["range"]
                if len(bounds) == 2:
                    range_str = f"{bounds[0]}-{bounds[1]}"
                    return EventMatcher.is_time_in_range(range_str, val_req)

        return False

    @staticmethod
    def match_component(event_attr: AttributeComponent, request_attr: AttributeComponent) -> bool:
        attr_event = event_attr.attribute.strip().lower()
        attr_req   = request_attr.attribute.strip().lower()

        # las etiquetas tienen que coincidir (rol con rol, etc)
        if attr_event != attr_req:
            return False

        val_event = event_attr.value if isinstance(event_attr.value, dict) else event_attr.value.strip().lower()
        val_req   = str(request_attr.value).strip().lower()

        return EventMatcher._match_value(val_event, val_req, attr_event)

    # el evento aplica solo si coinciden las 5 dimensiones
    @staticmethod
    def match_event(event: Event, request: AccessRequest) -> bool:
        return (
            EventMatcher.match_component(event.subject, request.subject) and
            EventMatcher.match_component(event.asset,   request.asset)   and
            EventMatcher.match_component(event.space,   request.space)   and
            EventMatcher.match_component(event.time,    request.time)    and
            EventMatcher.match_component(event.action,  request.action)
        )
