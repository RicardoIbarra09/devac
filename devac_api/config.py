import os
from typing import Optional

from pydantic import BaseModel, Field


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# todo se puede cambiar con variables DEVAC_*
class Settings(BaseModel):
    policy_file: Optional[str] = Field(None, description="Políticas iniciales (.json o .dpl)")
    similarity_threshold: float = 0.3
    stress_threshold: float = 0.5
    audit: bool = True
    conflict_priority: str = "deny"
    export_graph: bool = False  # exportar el gexf es I/O en cada build, apagado por defecto
    rules_module: Optional[str] = Field(
        None, description="Módulo importable con register(reconfigurator) para reglas de reconfiguración"
    )
    max_batch_size: int = 10_000

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            policy_file=os.getenv("DEVAC_POLICY_FILE") or None,
            similarity_threshold=float(os.getenv("DEVAC_SIMILARITY_THRESHOLD", "0.3")),
            stress_threshold=float(os.getenv("DEVAC_STRESS_THRESHOLD", "0.5")),
            audit=_env_bool("DEVAC_AUDIT", True),
            conflict_priority=os.getenv("DEVAC_CONFLICT_PRIORITY", "deny"),
            export_graph=_env_bool("DEVAC_EXPORT_GRAPH", False),
            rules_module=os.getenv("DEVAC_RULES_MODULE") or None,
            max_batch_size=int(os.getenv("DEVAC_MAX_BATCH_SIZE", "10000")),
        )
