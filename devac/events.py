import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional
from pydantic import BaseModel, Field
import uuid


# base de todos los eventos, los de cada dominio heredan de aqui
class DomainEvent(BaseModel):
    event_id:   str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    timestamp:  datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source:     str = "system"
    payload:    Dict[str, Any] = {}  # datos extra del evento


class AccesoDenegado(DomainEvent):
    event_type:  str = "acceso_denegado"
    subject_id:  str
    asset_id:    str
    space:       str = ""
    policy_id:   Optional[str] = None
    on_deny:     Optional[str] = None  # alert / escalate


class AccesoPermitido(DomainEvent):
    event_type: str = "acceso_permitido"
    subject_id: str
    asset_id:   str
    space:      str = ""
    policy_id:  Optional[str] = None


class SesionIniciada(DomainEvent):
    event_type: str = "sesion_iniciada"
    subject_id: str
    space:      str = ""


class SesionTerminada(DomainEvent):
    event_type: str = "sesion_terminada"
    subject_id: str
    space:      str = ""


class RolCambiado(DomainEvent):
    event_type: str = "rol_cambiado"
    subject_id: str
    rol_anterior: str
    rol_nuevo:    str


class RolRevocado(DomainEvent):
    event_type: str = "rol_revocado"
    subject_id: str
    rol:        str


class PoliticaAgregada(DomainEvent):
    event_type: str = "politica_agregada"
    policy_id:  str


class PoliticaEliminada(DomainEvent):
    event_type: str = "politica_eliminada"
    policy_id:  str


class PoliticaActualizada(DomainEvent):
    event_type: str = "politica_actualizada"
    policy_id:  str


class AlmacenCreado(DomainEvent):
    event_type: str = "almacen_creado"
    store_id:   str
    space:      str = ""


class AlmacenEliminado(DomainEvent):
    event_type: str = "almacen_eliminado"
    store_id:   str
    space:      str = ""


# los 11 eventos genericos
INFRASTRUCTURE_EVENTS: Dict[str, type] = {
    "acceso_denegado":    AccesoDenegado,
    "acceso_permitido":   AccesoPermitido,
    "sesion_iniciada":    SesionIniciada,
    "sesion_terminada":   SesionTerminada,
    "rol_cambiado":       RolCambiado,
    "rol_revocado":       RolRevocado,
    "politica_agregada":  PoliticaAgregada,
    "politica_eliminada": PoliticaEliminada,
    "politica_actualizada": PoliticaActualizada,
    "almacen_creado":     AlmacenCreado,
    "almacen_eliminado":  AlmacenEliminado,
}


Handler = Callable[[DomainEvent], Coroutine[Any, Any, None]]


# pub/sub en memoria, no guarda nada en disco
class EventBus:
    def __init__(self):
        self._subscribers: Dict[str, List[Handler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Handler) -> None:
        handlers = self._subscribers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    def handler(self, event_type: str):
        def decorator(fn: Handler) -> Handler:
            self.subscribe(event_type, fn)
            return fn
        return decorator

    async def publish(self, event: DomainEvent) -> None:
        specific = self._subscribers.get(event.event_type, [])
        wildcard = self._subscribers.get("*", [])  # los suscritos a * reciben todo
        all_handlers = specific + wildcard
        if all_handlers:
            # todos los handlers en paralelo
            await asyncio.gather(*[h(event) for h in all_handlers], return_exceptions=True)

    # para usarlo desde codigo sin event loop
    def publish_sync(self, event: DomainEvent) -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.publish(event))
            else:
                loop.run_until_complete(self.publish(event))
        except RuntimeError:
            asyncio.run(self.publish(event))
