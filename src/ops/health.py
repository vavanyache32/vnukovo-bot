"""FastAPI sidecar exposing /healthz, /readyz, /metrics."""
from __future__ import annotations

import asyncio

import uvicorn
from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from ..config import get_settings


def make_app() -> FastAPI:
    app = FastAPI(title="vnukovo-bot ops", version="0.1.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        # Could check DB / proxy reachability here
        return {"status": "ready"}

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


async def serve() -> None:
    s = get_settings()
    config = uvicorn.Config(
        make_app(),
        host="0.0.0.0",
        port=s.health_port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    await server.serve()


def run_in_background() -> asyncio.Task:
    return asyncio.create_task(serve(), name="health")
