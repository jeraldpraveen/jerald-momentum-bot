"""FastAPI app factory: routers, CORS, and domain-error -> HTTP translation."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .routers import (
    backtest,
    cycles,
    exit_flags,
    fundamental,
    holdings,
    journal,
    phase3,
    regime_gate,
    risk_caps,
    scanner,
    technical,
)
from .services.errors import DomainError

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def _lifespan(_: FastAPI):
    """A backtest left mid-flight by a restart can never finish — flag it, so the
    UI shows "interrupted" instead of polling a phantom job forever."""
    from .backtest import runs as backtest_runs
    n = backtest_runs.mark_stale_as_interrupted()
    if n:
        logging.getLogger(__name__).info("Marked %d interrupted backtest run(s)", n)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Momentum Rotation Workbench",
        version="0.2.0",
        description="Weekly, manually-driven momentum strategy — decision & journaling workbench.",
        lifespan=_lifespan,
    )

    # Vite dev server origins.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(DomainError)
    async def _domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())

    @app.get("/api/health", tags=["health"])
    def health() -> dict:
        return {"status": "ok"}

    app.include_router(holdings.router)
    app.include_router(backtest.router)
    app.include_router(cycles.router)
    app.include_router(regime_gate.router)
    app.include_router(scanner.router)
    app.include_router(exit_flags.router)
    app.include_router(fundamental.router)
    app.include_router(technical.router)
    app.include_router(risk_caps.router)
    app.include_router(journal.router)
    app.include_router(phase3.router)
    return app


app = create_app()
