# uso:
#   DEVAC_POLICY_FILE=experiments/nimbuslab_policies.json poetry run uvicorn devac_api.main:app --port 8000

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from devac_api.config import Settings
from devac_api.engine import DevacEngine, PolicyConflict, PolicyNotFound, RebuildInProgress
from devac_api.middleware import ProcessTimeMiddleware
from devac_api.routers import admin, evaluation, events, policies

# errores del engine -> codigo http
_ERROR_STATUS = {
    PolicyNotFound: status.HTTP_404_NOT_FOUND,
    PolicyConflict: status.HTTP_409_CONFLICT,
    RebuildInProgress: status.HTTP_409_CONFLICT,
}


# engine se puede pasar ya construido (pruebas o una replica)
def create_app(settings: Optional[Settings] = None, engine: Optional[DevacEngine] = None) -> FastAPI:
    settings = settings or (engine.settings if engine else Settings.from_env())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.engine is None:
            # el build inicial es pesado
            app.state.engine = await asyncio.to_thread(DevacEngine.from_settings, settings)
        yield
        await app.state.engine.aclose()

    app = FastAPI(
        title="DEvAC API",
        version="0.1.0",
        description="Evaluación de acceso por comunidades, gestión dinámica de políticas y eventos de dominio.",
        lifespan=lifespan,
    )
    app.state.engine = engine
    app.add_middleware(ProcessTimeMiddleware)

    for exc_type, code in _ERROR_STATUS.items():
        app.add_exception_handler(exc_type, _error_handler(code))

    app.include_router(admin.router)
    app.include_router(evaluation.router)
    app.include_router(policies.router)
    app.include_router(events.router)
    return app


def _error_handler(code: int):
    async def handler(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=code, content={"detail": str(exc)})
    return handler


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
