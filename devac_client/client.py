from typing import Any, Dict, List, Optional, Tuple

import httpx


class DevacAPIError(Exception):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class DevacClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        timeout: float = 10.0,
        max_connections: int = 100,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        # conexiones reutilizables
        limits = httpx.Limits(max_connections=max_connections, max_keepalive_connections=max_connections)
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, limits=limits, transport=transport)

    async def __aenter__(self) -> "DevacClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        response = await self._http.request(method, url, **kwargs)
        # errores http como excepcion
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text
            raise DevacAPIError(response.status_code, detail)
        return response

    async def evaluate(self, request: Dict[str, str], engine: str = "devac") -> dict:
        decision, _ = await self.evaluate_timed(request, engine)
        return decision

    async def evaluate_timed(self, request: Dict[str, str], engine: str = "devac") -> Tuple[dict, Optional[float]]:
        response = await self._request("POST", "/v1/evaluate", params={"engine": engine}, json=request)
        server_us = response.headers.get("x-process-time-us")  # lo pone el middleware del servidor
        return response.json(), float(server_us) if server_us else None

    async def evaluate_batch(self, requests: List[Dict[str, str]], engine: str = "devac") -> dict:
        response = await self._request("POST", "/v1/evaluate/batch", params={"engine": engine}, json={"requests": requests})
        return response.json()

    async def list_policies(self, offset: int = 0, limit: int = 100) -> dict:
        return (await self._request("GET", "/v1/policies", params={"offset": offset, "limit": limit})).json()

    async def get_policy(self, policy_id: str) -> dict:
        return (await self._request("GET", f"/v1/policies/{policy_id}")).json()

    async def add_policy(self, policy: dict) -> dict:
        return (await self._request("POST", "/v1/policies", json=policy)).json()

    async def add_policies(self, policies: List[dict]) -> dict:
        return (await self._request("POST", "/v1/policies/batch", json=policies)).json()

    async def add_dpl(self, source: str) -> dict:
        return (await self._request("POST", "/v1/policies/dpl", json={"source": source})).json()

    async def update_policy(self, policy: dict) -> dict:
        return (await self._request("PUT", f"/v1/policies/{policy['policy_id']}", json=policy)).json()

    async def delete_policy(self, policy_id: str) -> None:
        await self._request("DELETE", f"/v1/policies/{policy_id}")

    async def publish_event(self, event_type: str, *, wait: bool = True, payload: Optional[dict] = None, **fields) -> dict:
        body = {"event_type": event_type, "payload": payload or {}, **fields}
        return (await self._request("POST", "/v1/events", params={"wait": str(wait).lower()}, json=body)).json()

    async def health(self) -> dict:
        return (await self._request("GET", "/health")).json()

    async def stats(self) -> dict:
        return (await self._request("GET", "/v1/stats")).json()

    async def rebuild(self) -> dict:
        return (await self._request("POST", "/v1/admin/rebuild")).json()
