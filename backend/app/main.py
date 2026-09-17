"""OFC API entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.config import get_settings
from app.db.session import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    init_db()
    # In-process generate tasks die with the process; clear leftover mid-stage rows.
    from app.services.generate_jobs import sweep_stale_jobs_on_startup

    sweep_stale_jobs_on_startup()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="OFC — Offline Report Composer",
        description="Air-gapped LLM report pipeline with file + DB context",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router, prefix="/api")

    # Optional: serve built SPA when present (Docker / air-gap single port)
    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="spa")

    return app


app = create_app()
