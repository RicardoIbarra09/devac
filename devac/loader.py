from devac.models import Policy, AccessRequest
from typing import List
from pathlib import Path
import json


class PolicyLoader:
    @staticmethod
    def from_file(filepath: str) -> List[Policy]:
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"{filepath} not found.")
        with open(filepath, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
        return [Policy.model_validate(entry) for entry in raw_data]  # pydantic valida cada politica


class AccessRequestLoader:
    @staticmethod
    def from_file(filepath: str) -> List[AccessRequest]:
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"{filepath} not found.")
        with open(filepath, "r", encoding="utf-8") as f:
            raw_requests = json.load(f)
        return [AccessRequest.model_validate(entry) for entry in raw_requests]
