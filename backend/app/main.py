from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import text

from .api import router
from .db import engine
from .opt.solvers import available_solvers

logger = logging.getLogger("lensplan")
logging.basicConfig(level=logging.INFO, format="%(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    found = [s.value for s in available_solvers()]
    logger.info(
        '{"event":"startup","solvers":%s}',
        found,
    )
    if not found:
        logger.warning('{"event":"startup","warning":"no solver available"}')
    yield


app = FastAPI(
    title="LensPlan",
    version="1.0.0",
    description="Multi-plant lens production MIP on Gurobi or HiGHS.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3006",
        "http://127.0.0.1:3006",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def access_log(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - started) * 1000
    logger.info(
        '{"event":"request","id":"%s","method":"%s","path":"%s","status":%d,"ms":%.1f}',
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
    # pydantic ValidationError is a ValueError; do not map those to 422.
    if isinstance(exc, PydanticValidationError):
        logger.exception('{"event":"response_model_error"}')
        return JSONResponse(status_code=500, content={"detail": "internal server error"})
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


@app.get("/ready", tags=["meta"])
def ready() -> dict:
    """Readiness needs the database and at least one solver; liveness does not."""
    checks = {"database": False, "solver": False}
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
        checks["database"] = True
    except Exception as exc:  # pragma: no cover - depends on deployment
        logger.warning('{"event":"ready","database_error":"%s"}', exc)
    checks["solver"] = bool(available_solvers())

    ready_now = all(checks.values())
    return {"ready": ready_now, "checks": checks}


app.include_router(router)
