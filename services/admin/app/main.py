import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import autoconfig, dav_auth, db, dkim
from .api import router as api_router
from .config import settings
from .security import ensure_initial_admin

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mailstack")

STATIC = Path(__file__).parent / "static"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not settings.session_secret:
        raise RuntimeError("SESSION_SECRET must be set")
    db.wait_for_database()
    db.migrate()
    with db.transaction() as cur:
        ensure_initial_admin(cur)
    dkim.sync()
    log.info("MailStack admin ready for %s", settings.mail_hostname)
    yield


app = FastAPI(
    title="MailStack API",
    version="1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    # Cross-site forms cannot set custom headers, so requiring one blocks CSRF on cookie sessions.
    if (
        request.method not in SAFE_METHODS
        and request.url.path.startswith("/api/")
        and "x-api-key" not in request.headers
        and request.headers.get("x-requested-with") != "MailStack"
    ):
        return JSONResponse({"detail": "Missing X-Requested-With header"}, status_code=403)
    return await call_next(request)


app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="mailstack_session",
    max_age=settings.session_max_age,
    same_site="strict",
    https_only=True,
)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    messages = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", []) if p not in ("body", "query", "path"))
        messages.append(f"{loc}: {err.get('msg')}" if loc else err.get("msg", "Invalid input"))
    return JSONResponse({"detail": "; ".join(messages)}, status_code=422)


app.include_router(api_router)
app.include_router(autoconfig.router)
app.include_router(dav_auth.router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/healthz", include_in_schema=False)
def healthz():
    try:
        db.query_one("SELECT 1 AS ok")
    except Exception as exc:  # noqa: BLE001 - health endpoint reports any failure
        raise HTTPException(status_code=503, detail=f"database: {exc}")
    return {"ok": True}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html", headers={"Content-Security-Policy": CSP, "Cache-Control": "no-cache"})
