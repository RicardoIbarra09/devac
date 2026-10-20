from typing import Union, Dict, Any, List
from devac.models import Event, AttributeComponent, AccessRequest
from datetime import datetime


class EventMatcher:

    @staticmethod
    def is_time_in_range(policy_range: str, request_time: str) -> bool:
        try:
            start_str, end_str = policy_range.split("-")
            start_time = datetime.strptime(start_str.strip(), "%H:%M").time()
            end_time   = datetime.strptime(end_str.strip(),   "%H:%M").time()
            req_time   = datetime.strptime(request_time.strip(), "%H:%M").time()
            return start_time <= req_time <= end_time
        except ValueError:
            return False

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
            if val_event == "*":
                return True
            if attr == "rango_horario" and "-" in val_event:
                return EventMatcher.is_time_in_range(val_event, val_req)
            return val_event == val_req

        if isinstance(val_event, dict):
            if "in" in val_event:
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

        if attr_event != attr_req:
            return False

        val_event = event_attr.value if isinstance(event_attr.value, dict) else event_attr.value.strip().lower()
        val_req   = str(request_attr.value).strip().lower()

        return EventMatcher._match_value(val_event, val_req, attr_event)

    @staticmethod
    def match_event(event: Event, request: AccessRequest) -> bool:
        return (
            EventMatcher.match_component(event.subject, request.subject) and
            EventMatcher.match_component(event.asset,   request.asset)   and
            EventMatcher.match_component(event.space,   request.space)   and
            EventMatcher.match_component(event.time,    request.time)    and
            EventMatcher.match_component(event.action,  request.action)
        )
