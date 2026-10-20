# xolo/abac/events.py
"""
APEX — Taxonomía de eventos de dominio y EventBus asíncrono.

La taxonomía tiene dos capas:
  1. Infraestructura (este módulo): 11 eventos genéricos, válidos para cualquier dominio.
  2. Dominio (por proyecto): se extiende subclaseando DomainEvent.

Uso del EventBus:
    bus = EventBus()
    bus.subscribe("sesion_terminada", mi_handler)
    await bus.publish(SesionTerminada(subject_id="u1", space="sala-3"))
"""

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional
from pydantic import BaseModel, Field
import uuid


# ---------------------------------------------------------------------------
# Clase base
# ---------------------------------------------------------------------------

class DomainEvent(BaseModel):
    """
    Evento de dominio tipado.  Todos los eventos de infraestructura y de dominio
    extienden esta clase.

    Campos obligatorios en subclases:
      - event_type : identificador de tipo (snake_case), ej. "sesion_terminada"

    Campos opcionales:
      - source     : componente que generó el evento
      - payload    : datos adicionales específicos del evento
    """
    event_id:   str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    timestamp:  datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source:     str = "system"
    payload:    Dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Categoría 1 — Acceso  (2 eventos)
# ---------------------------------------------------------------------------

class AccesoDenegado(DomainEvent):
    """Se emite cuando el evaluador retorna effect='deny'."""
    event_type:  str = "acceso_denegado"
    subject_id:  str
    asset_id:    str
    space:       str = ""
    policy_id:   Optional[str] = None
    on_deny:     Optional[str] = None  # "alert" | "escalate"


class AccesoPermitido(DomainEvent):
    """Se emite cuando el evaluador retorna effect='allow'."""
    event_type: str = "acceso_permitido"
    subject_id: str
    asset_id:   str
    space:      str = ""
    policy_id:  Optional[str] = None


# ---------------------------------------------------------------------------
# Categoría 2 — Sesión  (2 eventos)
# ---------------------------------------------------------------------------

class SesionIniciada(DomainEvent):
    """Se emite cuando un sujeto inicia una sesión."""
    event_type: str = "sesion_iniciada"
    subject_id: str
    space:      str = ""


class SesionTerminada(DomainEvent):
    """Se emite cuando una sesión finaliza (expiración o cierre explícito)."""
    event_type: str = "sesion_terminada"
    subject_id: str
    space:      str = ""


# ---------------------------------------------------------------------------
# Categoría 3 — Rol  (2 eventos)
# ---------------------------------------------------------------------------

class RolCambiado(DomainEvent):
    """Se emite cuando el rol de un sujeto cambia."""
    event_type: str = "rol_cambiado"
    subject_id: str
    rol_anterior: str
    rol_nuevo:    str


class RolRevocado(DomainEvent):
    """Se emite cuando el rol de un sujeto es revocado sin asignar uno nuevo."""
    event_type: str = "rol_revocado"
    subject_id: str
    rol:        str


# ---------------------------------------------------------------------------
# Categoría 4 — Política  (3 eventos)
# ---------------------------------------------------------------------------

class PoliticaAgregada(DomainEvent):
    """Se emite cuando una nueva política es añadida al evaluador."""
    event_type: str = "politica_agregada"
    policy_id:  str


class PoliticaEliminada(DomainEvent):
    """Se emite cuando una política es eliminada del evaluador."""
    event_type: str = "politica_eliminada"
    policy_id:  str


class PoliticaActualizada(DomainEvent):
    """Se emite cuando una política existente es modificada."""
    event_type: str = "politica_actualizada"
    policy_id:  str


# ---------------------------------------------------------------------------
# Categoría 5 — Almacén Efímero  (2 eventos)
# ---------------------------------------------------------------------------

class AlmacenCreado(DomainEvent):
    """Se emite cuando se aprovisiona un almacén efímero (ej. namespace Redis)."""
    event_type: str = "almacen_creado"
    store_id:   str
    space:      str = ""


class AlmacenEliminado(DomainEvent):
    """Se emite cuando un almacén efímero es destruido o expira."""
    event_type: str = "almacen_eliminado"
    store_id:   str
    space:      str = ""


# ---------------------------------------------------------------------------
# Registro de todos los tipos de infraestructura (para validación y docs)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------

Handler = Callable[[DomainEvent], Coroutine[Any, Any, None]]


class EventBus:
    """
    Bus de eventos asíncrono en memoria (asyncio).

    - Sin persistencia en disco; el estado es efímero como el almacén que protege.
    - Suscripción por tipo exacto o con comodín "*" (recibe todos los eventos).
    - Publicación concurrente: todos los handlers de un evento se ejecutan con gather.

    Ejemplo:
        bus = EventBus()

        @bus.handler("rol_cambiado")
        async def on_rol(event: RolCambiado):
            print(f"Rol cambiado: {event.subject_id}")

        await bus.publish(RolCambiado(subject_id="u1", rol_anterior="medico", rol_nuevo="residente"))
    """

    def __init__(self):
        self._subscribers: Dict[str, List[Handler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """Registra un handler para un tipo de evento (o "*" para todos)."""
        self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Handler) -> None:
        """Elimina un handler registrado."""
        handlers = self._subscribers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    def handler(self, event_type: str):
        """Decorador para registrar handlers de forma declarativa."""
        def decorator(fn: Handler) -> Handler:
            self.subscribe(event_type, fn)
            return fn
        return decorator

    async def publish(self, event: DomainEvent) -> None:
        """
        Publica un evento y espera que todos sus handlers terminen.
        Los handlers del tipo exacto y los handlers '*' se ejecutan en paralelo.
        """
        specific = self._subscribers.get(event.event_type, [])
        wildcard = self._subscribers.get("*", [])
        all_handlers = specific + wildcard
        if all_handlers:
            await asyncio.gather(*[h(event) for h in all_handlers], return_exceptions=True)

    def publish_sync(self, event: DomainEvent) -> None:
        """
        Versión síncrona de publish para contextos sin event loop activo.
        Crea un loop temporal para ejecutar los handlers.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.publish(event))
            else:
                loop.run_until_complete(self.publish(event))
        except RuntimeError:
            asyncio.run(self.publish(event))
