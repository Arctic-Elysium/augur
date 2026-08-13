from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.auth.oidc import OIDCClient
from app.core.auth.session import SessionCodec
from app.core.config.settings import get_settings
from app.core.db.engine import dispose_engine, init_engine
from app.core.errors import AppError
from app.modules.base import build_registry
from app.platform.ai.backends.anthropic_backend import AnthropicBackend
from app.platform.ai.router import AIRouter, TokenLedger
from app.platform.observability.metrics import install_metrics


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_engine(settings)

    http = httpx.AsyncClient()
    app.state.http = http
    app.state.settings = settings
    app.state.oidc = OIDCClient(settings, http)
    app.state.session_codec = SessionCodec(settings)

    # AI gateway. Routing config is validated here, so a bad route fails the
    # pod's readiness probe rather than a player's turn.
    # Three tiers. Extraction and summarisation are structured, mechanical
    # tasks with no prose quality bar, so they run on the cheap model - they
    # fire on every single turn, which makes them the largest avoidable cost
    # in a long session.
    backends = {
        "claude_fast": AnthropicBackend(
            settings.anthropic_api_key, "claude-haiku-4-5-20251001",
            settings.ai_request_timeout_seconds,
        ),
        "claude": AnthropicBackend(
            settings.anthropic_api_key, "claude-sonnet-4-6",
            settings.ai_request_timeout_seconds,
        ),
        "claude_deep": AnthropicBackend(
            settings.anthropic_api_key, "claude-opus-4-6",
            settings.ai_request_timeout_seconds,
        ),
    }
    app.state.ai = AIRouter.from_config(
        settings.ai_config_path, backends, TokenLedger(settings.ai_session_token_budget)
    )

    yield

    await http.aclose()
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    registry = build_registry()
    registry.import_all_models()

    app = FastAPI(
        title="Augur",
        description="Reads the signs. Remembers the telling.",
        version="0.1.0",
        lifespan=lifespan,
        # No public docs outside local - carried over from the Tome review.
        docs_url="/docs" if settings.environment == "local" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.environment == "local" else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    api = APIRouter(prefix="/api")
    registry.mount(api)
    app.include_router(api)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        return {"status": "ok", "service": settings.project_slug}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(request: Request) -> JSONResponse:
        """Readiness. Reports *why* it is not ready rather than raising.

        Reached before lifespan completes, and if the AI router failed its
        startup validation, `state.ai` never gets set. An AttributeError here
        would surface as a 500 with a stack trace in the logs, which is a
        confusing way to learn that ai_routing.yaml has a bad backend.
        """
        state = request.app.state
        ai = getattr(state, "ai", None)
        db = getattr(state, "settings", None) is not None
        spa = getattr(state, "spa_mounted", None)
        ready = ai is not None and db
        payload = {
            "status": "ready" if ready else "starting",
            "ai": ai is not None,
            "config": db,
            "frontend": spa,
        }
        if getattr(state, "spa_error", None):
            payload["frontend_error"] = state.spa_error
        return JSONResponse(status_code=200 if ready else 503, content=payload)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        """Normalise FastAPI's validation errors onto the app's error shape.

        FastAPI returns {"detail": [...]} while everything else returns
        {"code", "message"}, so the client fell back to the bare status text and
        every schema rejection surfaced as "Unprocessable Entity" - true, and
        completely useless for working out which field was wrong.
        """
        problems = []
        for err in exc.errors():
            where = ".".join(str(p) for p in err["loc"] if p != "body")
            problems.append(f"{where}: {err['msg']}" if where else err["msg"])
        return JSONResponse(
            status_code=422,
            content={
                "code": "invalid_request",
                "message": "; ".join(problems[:4]) or "invalid request",
                "detail": {"errors": problems},
            },
        )

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "detail": exc.detail},
        )

    if settings.metrics_enabled:
        install_metrics(app)

    _mount_spa(app, settings)

    return app


def _mount_spa(app: FastAPI, settings) -> None:
    """Serve the built frontend, with history fallback.

    Mounted last so it never shadows /api, /healthz, /readyz or /metrics.
    Unknown paths return index.html rather than 404 because client-side routes
    like /play/<id> exist only in the browser - a hard refresh on one must not
    404. Unknown /api paths still 404 properly, which matters for debugging.
    """
    if not settings.static_dir:
        # Local dev: Vite serves the SPA, so the API must not claim every path.
        app.state.spa_mounted = False
        return

    static_dir = Path(settings.static_dir)
    index = static_dir / "index.html"

    if not index.is_file():
        # STATIC_DIR was configured but the bundle is not there. Failing loudly
        # matters: silently skipping the mount produces an app that starts,
        # passes health checks, and 404s every page - which looks like a
        # routing or ingress problem and sends you looking in the wrong place.
        app.state.spa_mounted = False
        app.state.spa_error = f"STATIC_DIR={static_dir} has no index.html"
        print(
            f"WARNING: {app.state.spa_error}. The API will serve /api but no "
            f"frontend. This usually means the image was built without the "
            f"web stage - check `docker run --rm --entrypoint sh IMAGE "
            f"-c 'ls /srv/static'`.",
            flush=True,
        )
        return

    app.state.spa_mounted = True

    assets = static_dir / "assets"
    if assets.is_dir():
        # Vite fingerprints asset filenames, so they are safe to cache forever.
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    reserved = ("api", "healthz", "readyz", "metrics", "docs", "openapi.json")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        # An unknown /api path must 404, not quietly return HTML - otherwise a
        # typo'd endpoint looks like a frontend bug and the client's JSON parse
        # fails somewhere far from the cause.
        if path.split("/", 1)[0] in reserved:
            return JSONResponse(
                status_code=404, content={"code": "not_found", "message": path}
            )
        candidate = static_dir / path
        if path and candidate.is_file() and candidate.resolve().is_relative_to(
            static_dir.resolve()
        ):
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
