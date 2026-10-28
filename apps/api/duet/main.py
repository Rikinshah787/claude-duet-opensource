"""FastAPI application entrypoint.

`uvicorn duet.main:app --port 8787`
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import apps as apps_router
from .api import oauth_routes
from .api import system as system_router
from .api import thread as thread_router
from .api import work as work_router
from .config import settings
from .core.scheduler import scheduler
from .db import init_db

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("duet")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    log.info("database ready at %s", settings.database_url)

    # The scheduler is what makes this proactive. Disable it for tests and for
    # CLI use, where an autonomous background loop is surprising.
    if os.environ.get("DUET_DISABLE_SCHEDULER", "").lower() not in {"1", "true", "yes"}:
        scheduler.start()
    else:
        log.info("scheduler disabled by DUET_DISABLE_SCHEDULER")

    from .core.telemetry import instrument

    if instrument(app):
        log.info("tracing exported via OTLP")

    ok, detail = __import__("duet.llm.client", fromlist=["get_llm"]).get_llm().available()
    log.info("model layer: %s (%s)", detail, "ready" if ok else "unavailable, using templates")

    yield

    scheduler.stop()


app = FastAPI(
    title="Claude Duet (local)",
    description=(
        "A proactive executive assistant that monitors your connected apps, "
        "decides what needs you, and holds every consequential action for approval."
    ),
    version=system_router.VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    """Never leak a stack trace to the client; always leave one in the log."""
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}. Check the server log."},
    )


for router in (
    thread_router.router,
    work_router.router,
    apps_router.router,
    oauth_routes.router,
    system_router.router,
):
    app.include_router(router, prefix="/api")


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {
        "name": "Claude Duet (local)",
        "docs": "/docs",
        "health": "/api/health",
        "web": "http://localhost:3000",
    }


def run() -> None:
    """`python -m duet.main` for a one-liner start."""
    import uvicorn

    uvicorn.run(
        "duet.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    run()
