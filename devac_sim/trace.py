import json
from pathlib import Path
from typing import Iterable, List, Tuple

from devac.events import INFRASTRUCTURE_EVENTS, DomainEvent

DIMENSIONS = ("subject", "asset", "space", "time", "action")

ACCESS = "access"
POLICY_ADD = "policy_add"
POLICY_UPDATE = "policy_update"
POLICY_DELETE = "policy_delete"
DOMAIN_EVENT = "domain_event"
CONTROL_KINDS = (POLICY_ADD, POLICY_UPDATE, POLICY_DELETE, DOMAIN_EVENT)
KINDS = (ACCESS,) + CONTROL_KINDS

# campo obligatorio de cada tipo de linea
_REQUIRED = {
    ACCESS: "request",
    POLICY_ADD: "policy",
    POLICY_UPDATE: "policy",
    POLICY_DELETE: "policy_id",
    DOMAIN_EVENT: "event",
}


class TraceError(ValueError):
    pass


def _check(record: dict, line_no: int) -> None:
    kind = record.get("kind")
    if kind not in KINDS:
        raise TraceError(f"línea {line_no}: kind desconocido {kind!r}")
    if not isinstance(record.get("t"), (int, float)) or record["t"] < 0:
        raise TraceError(f"línea {line_no}: 't' debe ser un número >= 0")
    if _REQUIRED[kind] not in record:
        raise TraceError(f"línea {line_no}: falta '{_REQUIRED[kind]}' en un registro {kind}")
    if kind == ACCESS:
        missing = [d for d in DIMENSIONS if d not in record["request"]]
        if missing:
            raise TraceError(f"línea {line_no}: a la petición le faltan {missing}")


# primera linea opcional con kind=meta
def read_trace(path: str) -> Tuple[dict, List[dict]]:
    meta: dict = {}
    records: List[dict] = []
    last_t = 0.0
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("kind") == "meta":
            if records or meta:
                raise TraceError(f"línea {line_no}: meta solo puede ir al inicio")
            meta = record
            continue
        _check(record, line_no)
        if record["t"] < last_t:
            raise TraceError(f"línea {line_no}: la traza no está ordenada por t")
        last_t = record["t"]
        records.append(record)
    return meta, records


def write_trace(path: str, meta: dict, records: Iterable[dict]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "meta", **meta}, ensure_ascii=False) + "\n")
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# misma validacion que POST /v1/events
def build_domain_event(data: dict) -> DomainEvent:
    event_cls = INFRASTRUCTURE_EVENTS.get(data.get("event_type"), DomainEvent)
    return event_cls.model_validate(data)
