"""FastAPI app for ARI Disease Metadata Manager.

Assembly only: this builds the app, installs the middleware, and mounts the
routers. The behaviour lives in the modules it pulls together —

    config      every setting read at startup
    sessions    the token store, and who the caller is
    workspace   each curator's own working copy of the ontology
    stores      the assignment and id-provenance ledgers
    routes/     the endpoints, grouped by the page they serve
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import config, routes, sessions, workspace
from .errors import Invalid, NotFound

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Background task: periodically sweep idle per-user working copies so disk
    use stays bounded. The task is cancelled on shutdown."""
    async def _sweep_loop():
        while True:
            workspace._sweep_user_data()
            sessions._sweep_sessions()
            await asyncio.sleep(6 * 3600)   # every 6 hours
    task = asyncio.create_task(_sweep_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="ARI Metadata Manager", lifespan=lifespan)

# Registration order is reversed at request time: the last middleware added is
# the outermost. SessionMiddleware goes on first so it stays innermost and has
# populated `request.session` by the time a route runs.
app.add_middleware(
    SessionMiddleware,
    secret_key=config._session_secret(),
    same_site="lax",
    https_only=config.APP_BASE_URL.startswith("https"),
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Baseline response headers.

    Set in the app rather than only in nginx so a local or non-nginx deployment
    is covered too, and so the policy lives next to the markup it constrains.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self'; "                 # the CDN libraries are vendored now
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-src https:; "          # the review panel embeds source databases
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'",
    )
    return response


@app.middleware("http")
async def cache_policy(request: Request, call_next):
    """Revalidate the HTML; let versioned assets actually cache.

    Real care went into ``__ASSETV__``: one git-derived token, substituted at
    render, tagging every asset so a deploy busts all of them at once. Then this
    middleware set ``no-cache`` on **every** ``.js`` and ``.css`` response, so
    nothing was cached and the token did nothing — every page load re-downloaded
    all thirteen JS files and the stylesheets, which is the slowest part of
    opening a record on the modest laptops this audience uses.

    The HTML carries the token, so it must always be revalidated. An asset
    *requested with* a version token is immutable by construction: a new deploy
    changes the token, which changes the URL.
    """
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    elif path.endswith((".css", ".js")):
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if request.url.query.startswith("v=")
            else "no-cache, must-revalidate")
    return response


@app.get("/healthz")
async def healthz():
    """Liveness and load state, for nginx and for a human debugging a report.

    Nothing here needs a session: it reports no curation content, only whether
    the process is serving and how much it is holding."""
    try:
        loaded = len(workspace.BASE.get_diseases_list())
    except Exception as e:                     # a failed ontology load is the thing to catch
        log.error("Health check could not read the base ontology: %s", e)
        return JSONResponse(status_code=503, content={
            "ok": False, "detail": "base ontology is not loadable", "version": config.APP_VERSION})
    return {
        "ok": True,
        "version": config.APP_VERSION,
        "diseases": loaded,
        "working_copies": len(list(config.USER_DIR.glob("*.owl"))) if config.USER_DIR.exists() else 0,
        "worlds_in_memory": len(workspace.USER_SVC),
        "sessions": len(sessions.SESSIONS),
        "github_enabled": config.GH_ENABLED,
    }


@app.exception_handler(NotFound)
async def not_found(request: Request, exc: NotFound):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(Invalid)
async def invalid(request: Request, exc: Invalid):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def bad_request(request: Request, exc: ValueError):
    """A refusal the caller can act on.

    ``NotFound`` and ``Invalid`` above are the deliberate ones. A bare
    ``ValueError`` from the service layer is still a refusal, so it keeps its
    400 — but a bare ``KeyError`` is no longer mapped at all: it is a dictionary
    bug, and it now 500s with a logged traceback instead of becoming a 404
    carrying an internal key name."""
    return JSONResponse(status_code=400, content={"detail": str(exc)})


for _router in routes.ROUTERS:
    app.include_router(_router)

# Last: the catch-all mount serves the js/css/asset files that the HTML pages
# registered above reference. Anything added after this would be unreachable.
app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="static")
