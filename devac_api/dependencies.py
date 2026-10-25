from fastapi import Request

from devac_api.engine import DevacEngine


def get_engine(request: Request) -> DevacEngine:
    return request.app.state.engine
