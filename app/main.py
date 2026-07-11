"""FastAPI app for ARI Disease Metadata Manager."""
import os
import json
import logging
import time
import shutil
import asyncio
import secrets
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Body
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .ontology_service import OntologyService
from . import github_service as gh
from . import export_service
from . import diff_service
from . import sssom_service
from . import xref_registry

log = logging.getLogger(__name__)


def _load_dotenv():
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

ONTOLOGY_FILE = os.environ.get(
    "ARI_ONTOLOGY_FILE",
    str(Path(__file__).resolve().parent.parent / "ontologies" / "ari_t1d.owl")
)

BASE = OntologyService(ONTOLOGY_FILE)   # shared, source-branch baseline


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Background task: periodically sweep idle per-user working copies so disk
    use stays bounded. The task is cancelled on shutdown."""
    async def _sweep_loop():
        while True:
            _sweep_user_data()
            await asyncio.sleep(6 * 3600)   # every 6 hours
    task = asyncio.create_task(_sweep_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="ARI Metadata Manager", lifespan=lifespan)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _app_version() -> str:
    """Manager version derived from git so it bumps on every update/deploy."""
    root = Path(__file__).resolve().parent.parent  # app repo root
    try:
        g = lambda *a: subprocess.check_output(["git", "-C", str(root), *a],
                                               text=True, stderr=subprocess.DEVNULL).strip()
        return f"2.{g('rev-list', '--count', 'HEAD')} ({g('show', '-s', '--format=%cd', '--date=short', 'HEAD')})"
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("Could not derive app version from git: %s", e)
        return "2.x"


APP_VERSION = _app_version()


def _asset_version() -> str:
    """Single cache-busting token for every static asset, changing on each deploy.

    The HTML pages tag all their js/css with `?v=__ASSETV__`, which is replaced
    with this token when the page is served. One value busts every asset at once,
    so there are no fragile per-file `?v=N` numbers to bump (and merge-conflict)."""
    root = Path(__file__).resolve().parent.parent
    try:
        n = subprocess.check_output(["git", "-C", str(root), "rev-list", "--count", "HEAD"],
                                    text=True, stderr=subprocess.DEVNULL).strip()
        if n:
            return n
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("Could not derive asset version from git, using time fallback: %s", e)
    return str(int(time.time()))   # fallback: bust on each restart


ASSET_VERSION = _asset_version()

# ----------------------------------------------------------------- GitHub config
GH_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GH_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
GH_OWNER = os.environ.get("GITHUB_OWNER", "")
GH_REPO = os.environ.get("GITHUB_REPO", "")
GH_BASE_BRANCH = os.environ.get("GITHUB_BASE_BRANCH", "main")
GH_ONTOLOGY_PATH = os.environ.get(
    "GITHUB_ONTOLOGY_PATH", "ontologies/ari_t1d.owl")
MAPPINGS_SSSOM_PATH = os.environ.get("GITHUB_SSSOM_PATH", "mappings/ari.sssom.tsv")
MAPPINGS_EQUIV_PATH = os.environ.get("GITHUB_EQUIV_PATH", "mappings/ari.equivalencies.tsv")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8001").rstrip("/")
OAUTH_CALLBACK_PATH = os.environ.get("OAUTH_CALLBACK_PATH", "/auth/github/callback")
ALLOWED_LOGINS = [s.strip() for s in os.environ.get("ALLOWED_LOGINS", "").split(",") if s.strip()]
REDIRECT_URI = APP_BASE_URL + OAUTH_CALLBACK_PATH
GH_ENABLED = bool(GH_CLIENT_ID and GH_CLIENT_SECRET and GH_OWNER and GH_REPO)

# Tokens are kept SERVER-SIDE (the signed session cookie holds only an opaque id),
# so the GitHub access token never reaches the browser.
SESSIONS_FILE = Path(__file__).resolve().parent.parent / ".sessions.json"


def _load_sessions() -> dict:
    if not SESSIONS_FILE.exists():
        return {}
    try:
        return json.loads(SESSIONS_FILE.read_text())
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Could not read %s (%s); starting with an empty session store", SESSIONS_FILE.name, e)
        return {}


def _save_sessions():
    try:
        SESSIONS_FILE.write_text(json.dumps(SESSIONS))
        os.chmod(SESSIONS_FILE, 0o600)
    except OSError as e:
        log.warning("Could not persist sessions to %s (%s); a restart will sign users out", SESSIONS_FILE.name, e)


# Server-side token store, persisted to disk so a restart (e.g. the auto-update
# timer) does not sign everyone out mid-session.
SESSIONS: dict[str, dict] = _load_sessions()

# Runtime settings (in-memory): which branch we populate FROM and PR INTO,
# and whether the local ontology file has unpublished edits.
STATE = {"source_branch": GH_BASE_BRANCH, "pr_base": GH_BASE_BRANCH}


def reload_base():
    global BASE
    BASE = OntologyService(ONTOLOGY_FILE)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", secrets.token_hex(32)),
    same_site="lax",
    https_only=APP_BASE_URL.startswith("https"),
)


def _user(request: Request):
    return SESSIONS.get(request.session.get("sid", ""))


# ---- per-user working copies: each signed-in user edits their OWN ontology copy
USER_DIR = Path(__file__).resolve().parent.parent / ".user-data"
USER_DATA_TTL_DAYS = int(os.environ.get("USER_DATA_TTL_DAYS", "14"))
USER_SVC: dict = {}
USER_DIRTY: set = set()


def _login(request: Request):
    u = _user(request)
    return u["identity"]["login"] if u else None


def user_service(login, create=False):
    if not login:
        return BASE
    if login in USER_SVC:
        return USER_SVC[login]
    if create:
        USER_DIR.mkdir(parents=True, exist_ok=True)
        f = USER_DIR / f"{login}.owl"
        shutil.copy2(ONTOLOGY_FILE, f)            # snapshot the current base for this user
        USER_SVC[login] = OntologyService(str(f))
        return USER_SVC[login]
    return BASE


def service_for(request: Request, write=False):
    """The ontology a request should read/write: a signed-in user's private copy
    once they have started editing, otherwise the shared base."""
    login = _login(request)
    if write:
        return user_service(login, create=True)
    if login and login in USER_SVC:
        return USER_SVC[login]
    return BASE


def _reset_user(login):
    USER_SVC.pop(login, None)
    USER_DIRTY.discard(login)
    try:
        (USER_DIR / f"{login}.owl").unlink()
    except FileNotFoundError:
        pass                                    # never edited — nothing to reset
    except OSError as e:
        log.warning("Could not delete working copy for %s: %s", login, e)


def _mark_dirty(request: Request):
    login = _login(request)
    if login:
        USER_DIRTY.add(login)


def _dirty(request: Request):
    login = _login(request)
    return bool(login and login in USER_DIRTY)


def _sweep_user_data():
    """Delete per-user working copies idle longer than the TTL (bounds disk use).
    A copy that is actively edited keeps a recent mtime, so it is not swept."""
    if USER_DATA_TTL_DAYS <= 0 or not USER_DIR.exists():
        return
    cutoff = time.time() - USER_DATA_TTL_DAYS * 86400
    for f in USER_DIR.glob("*.owl"):
        try:
            if f.stat().st_mtime < cutoff:
                login = f.stem
                USER_SVC.pop(login, None)
                USER_DIRTY.discard(login)
                f.unlink()
        except OSError as e:
            log.warning("Could not sweep idle working copy %s: %s", f.name, e)


@app.middleware("http")
async def no_cache_assets(request: Request, call_next):
    """Always revalidate the app's HTML/CSS/JS so edits are picked up on reload."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".html", ".css", ".js")):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


@app.exception_handler(KeyError)
async def not_found(request: Request, exc: KeyError):
    return JSONResponse(status_code=404, content={"detail": str(exc.args[0])})


@app.exception_handler(ValueError)
async def bad_request(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/api/v2/overview")
async def overview(request: Request):
    return {**service_for(request).overview(), "app_version": APP_VERSION}


@app.get("/api/v2/diseases")
async def diseases_list(request: Request):
    return service_for(request).get_diseases_list()


@app.get("/api/v2/tree/alphabetical")
async def alphabetical_tree(request: Request):
    return service_for(request).get_alphabetical_tree()


@app.get("/api/v2/tree/tissue")
async def tissue_tree(request: Request):
    return service_for(request).get_tissue_hierarchy()


@app.get("/api/v2/symptoms")
async def symptoms_index(request: Request):
    return service_for(request).get_symptoms_index()


@app.get("/api/v2/schema")
async def schema(request: Request):
    """Field schema for editable disease-data item categories."""
    return service_for(request).get_schema()


@app.get("/api/v2/disease/{iri:path}")
async def disease_detail(request: Request, iri: str):
    return service_for(request).get_disease_detail(iri)


@app.put("/api/v2/disease/{iri:path}")
async def update_disease(request: Request, iri: str, payload: dict = Body(...)):
    """Edit disease fields. Body: {"changes": {...}, "editor": "name"}."""
    changes = payload.get("changes", payload)
    editor = payload.get("editor", "user")
    r = service_for(request, write=True).update_disease(iri, changes, editor=editor)
    _mark_dirty(request)
    return r


@app.post("/api/v2/disease/{iri:path}/item")
async def add_item(request: Request, iri: str, payload: dict = Body(...)):
    """Add a data item to a disease. Body: {category, values:{...}, editor}."""
    r = service_for(request, write=True).add_item(iri, payload["category"], payload.get("values", {}),
                         editor=payload.get("editor", "user"))
    _mark_dirty(request)
    return r


@app.put("/api/v2/item/{iri:path}")
async def update_item(request: Request, iri: str, payload: dict = Body(...)):
    """Edit a data item. Body: {category, changes:{...}, disease, editor}."""
    r = service_for(request, write=True).update_item(iri, payload["category"], payload.get("changes", {}),
                            disease_iri=payload.get("disease", ""),
                            editor=payload.get("editor", "user"))
    _mark_dirty(request)
    return r


@app.delete("/api/v2/item/{iri:path}")
async def delete_item(request: Request, iri: str, payload: dict = Body(...)):
    """Delete a data item. Body: {category, disease, editor}."""
    r = service_for(request, write=True).delete_item(iri, payload.get("category", ""),
                            payload["disease"], editor=payload.get("editor", "user"))
    _mark_dirty(request)
    return r


@app.get("/api/v2/releases")
async def releases_list(request: Request):
    svc = service_for(request)
    return {"current": svc._current_version(), "releases": svc.list_releases()}


@app.post("/api/v2/releases")
async def create_release(request: Request, payload: dict = Body(default={})):
    """Admin action: cut a versioned release snapshot of the ontology."""
    version = payload.get("version", "")
    notes = payload.get("notes", "")
    editor = payload.get("editor", "admin")
    return service_for(request, write=True).create_release(version=version, notes=notes, editor=editor)


@app.get("/api/v2/xrefs")
async def xrefs(request: Request):
    """All diseases with their database cross-references, for the reference-review page."""
    return service_for(request).get_xref_rows()


@app.get("/api/v2/xref-databases")
async def xref_databases():
    """Cross-reference database registry (labels, CURIE prefixes, link-out URL
    templates). The single source both frontend pages build their columns and
    link-outs from, kept in step with the SSSOM prefixes on the server."""
    return xref_registry.public_list()


@app.get("/api/v2/mappings")
async def mappings(request: Request):
    """Already-curated positive/negative cross-reference judgments.

    Read from the accumulated SSSOM (falling back to the equivalencies file) so
    the review page can pre-highlight cells that were confirmed or flagged in an
    earlier session. When signed in, the files are read from the current source
    branch on GitHub; otherwise the local working-tree copy (if any) is used.
    """
    sssom_text = equiv_text = ""
    u = _user(request) if GH_ENABLED else None
    if u:
        async def _read(path):
            try:
                return (await gh.get_file_at(u["token"], GH_OWNER, GH_REPO, path, STATE["source_branch"])).decode("utf-8")
            except Exception as e:
                log.debug("Could not read %s@%s from GitHub, falling back to local: %s", path, STATE["source_branch"], e)
                return ""
        sssom_text = await _read(MAPPINGS_SSSOM_PATH)
        equiv_text = await _read(MAPPINGS_EQUIV_PATH)
    if not sssom_text and not equiv_text:
        root = Path(__file__).resolve().parent.parent
        for attr, p in (("sssom_text", MAPPINGS_SSSOM_PATH), ("equiv_text", MAPPINGS_EQUIV_PATH)):
            try:
                txt = (root / p).read_text(encoding="utf-8")
            except OSError as e:
                log.debug("Could not read local mapping file %s: %s", p, e)
                txt = ""
            if attr == "sssom_text":
                sssom_text = txt
            else:
                equiv_text = txt
    return sssom_service.load_judgments(sssom_text, equiv_text)


@app.get("/api/v2/tissues")
async def tissues_list(request: Request):
    """All tissue-target individuals for new-disease creation forms."""
    return service_for(request).get_tissues()


@app.post("/api/v2/disease")
async def create_disease(request: Request, payload: dict = Body(...)):
    """Create a new disease individual. Body: {data: {...}, editor: str}."""
    data = payload.get("data", {})
    editor = payload.get("editor", "user")
    r = service_for(request, write=True).create_disease(data, editor=editor)
    _mark_dirty(request)
    return r


@app.get("/api/v2/search")
async def search(request: Request, q: str = ""):
    return service_for(request).search(q)


# ----------------------------------------------------------------- FEEDBACK
@app.get("/api/v2/feedback")
async def feedback_list(disease: str = ""):
    return BASE.feedback.list(disease or None)


@app.post("/api/v2/feedback")
async def feedback_add(payload: dict = Body(...)):
    """Add feedback for a term. Body: {disease, term, message, keep, author}."""
    return BASE.feedback.add(
        payload.get("disease", ""), payload.get("term", ""), payload.get("message", ""),
        keep=payload.get("keep", False), author=payload.get("author", "anonymous"))


@app.put("/api/v2/feedback/{fid}")
async def feedback_update(fid: str, payload: dict = Body(...)):
    """Edit feedback. Body: {message?, keep?, author?}."""
    return BASE.feedback.update(fid, message=payload.get("message"),
                                   keep=payload.get("keep"), author=payload.get("author"))


@app.delete("/api/v2/feedback/{fid}")
async def feedback_delete(fid: str):
    return BASE.feedback.delete(fid)


# ----------------------------------------------------------------- GITHUB AUTH + PUBLISH
@app.get("/api/v2/open-prs")
async def open_prs(request: Request):
    """Open pull requests in the repo, so the disease page can surface unmerged /
    unreviewed changes (issue #19). Works unauthenticated for a public repo; a
    signed-in user's token is used when available (e.g. private repos, rate limit).
    The frontend matches a PR to a disease via its `edit/<login>/<slug>-<ts>` branch."""
    if not GH_ENABLED:
        return {"github_enabled": False, "prs": []}
    u = _user(request)
    token = u["token"] if u else None
    try:
        prs = await gh.list_open_prs(token, GH_OWNER, GH_REPO)
    except Exception:
        prs = []
    return {"github_enabled": True, "prs": prs}


@app.get("/api/v2/me")
async def me(request: Request):
    if not GH_ENABLED:
        return {"github_enabled": False, "authenticated": False}
    u = _user(request)
    if not u:
        return {"github_enabled": True, "authenticated": False}
    i = u["identity"]
    return {"github_enabled": True, "authenticated": True,
            "login": i["login"], "name": i["name"], "avatar": i["avatar"],
            "repo": f"{GH_OWNER}/{GH_REPO}", "base_branch": GH_BASE_BRANCH}


def _safe_next(nxt: str) -> str:
    """Only allow same-origin relative paths (avoid open redirects)."""
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else "/"


@app.get("/auth/github")
async def auth_github(request: Request, next: str = "/"):
    if not GH_ENABLED:
        return JSONResponse(status_code=404, content={"detail": "GitHub integration not configured"})
    st = secrets.token_hex(16)
    request.session["oauth_state"] = st
    request.session["oauth_next"] = _safe_next(next)
    return RedirectResponse(gh.authorize_url(GH_CLIENT_ID, REDIRECT_URI, st))


@app.get(OAUTH_CALLBACK_PATH)
async def auth_callback(request: Request, code: str = "", state: str = ""):
    if not GH_ENABLED:
        return JSONResponse(status_code=404, content={"detail": "GitHub integration not configured"})
    if not code or state != request.session.get("oauth_state"):
        return JSONResponse(status_code=400, content={"detail": "Invalid OAuth state"})
    token = await gh.exchange_code(GH_CLIENT_ID, GH_CLIENT_SECRET, code, REDIRECT_URI)
    identity = await gh.get_identity(token)
    if ALLOWED_LOGINS and identity["login"] not in ALLOWED_LOGINS:
        return JSONResponse(status_code=403, content={"detail": f"@{identity['login']} is not allowed"})
    sid = secrets.token_urlsafe(24)
    SESSIONS[sid] = {"token": token, "identity": identity}
    _save_sessions()
    request.session["sid"] = sid
    request.session.pop("oauth_state", None)
    return RedirectResponse(_safe_next(request.session.pop("oauth_next", "/")))


@app.post("/api/v2/logout")
async def logout(request: Request):
    SESSIONS.pop(request.session.pop("sid", ""), None)
    _save_sessions()
    return {"ok": True}


@app.post("/api/v2/publish")
async def publish(request: Request, payload: dict = Body(default={})):
    """Commit the current ontology file to GitHub as the signed-in user (PR)."""
    if not GH_ENABLED:
        raise ValueError("GitHub integration is not configured")
    u = _user(request)
    if not u:
        return JSONResponse(status_code=401, content={"detail": "Sign in with GitHub first"})
    disease = payload.get("disease") or "ontology"
    message = payload.get("message") or f"Update {disease}"
    comment = (payload.get("comment") or "").strip()

    # Confirmed (positive) + flagged (negative) cross-references from a
    # reference-review session (also written to the mapping files further below).
    confirmed = payload.get("confirmed") or []
    flagged = payload.get("flagged") or []
    author = payload.get("author") or f"github:{u['identity']['login']}"

    # Record the review in each affected disease's changelog before snapshotting
    # the ontology for the commit. A write copy is used so the entries land in
    # this user's working ontology, mirroring how field edits are handled.
    svc = service_for(request, write=True) if (confirmed or flagged) else service_for(request)
    if confirmed or flagged:
        svc.log_xref_review(confirmed, flagged, editor=u["identity"]["login"])
        _mark_dirty(request)
    content = svc.path.read_bytes()

    # Diff current vs the source branch to summarise previous -> new values.
    import tempfile, os as _os
    summary = ""
    tmp_path = None
    try:
        data = await gh.get_file_at(u["token"], GH_OWNER, GH_REPO, GH_ONTOLOGY_PATH, STATE["source_branch"])
        tf = tempfile.NamedTemporaryFile(suffix=".owl", delete=False)
        tf.write(data); tf.close(); tmp_path = tf.name
        baseline = OntologyService(tmp_path)
        summary = diff_service.build_change_summary(svc, baseline)
    except Exception as e:
        log.warning("Could not build change summary against source branch %s: %s", STATE["source_branch"], e)
        summary = "_Change summary unavailable (could not load the source-branch baseline)._"
    finally:
        if tmp_path:
            try:
                _os.unlink(tmp_path)
            except OSError as e:
                log.debug("Could not remove temp baseline %s: %s", tmp_path, e)

    # Confirmed / flagged cross-references also accumulate into the
    # SSSOM + equivalencies mapping files.
    reuse_branch = payload.get("branch") or None
    labels = payload.get("labels") or ["edit term"]
    extra_files = {}
    SS_PATH = MAPPINGS_SSSOM_PATH
    EQ_PATH = MAPPINGS_EQUIV_PATH
    map_note = ""
    if confirmed or flagged:
        async def _read(path):
            try:
                return (await gh.get_file_at(u["token"], GH_OWNER, GH_REPO, path, STATE["source_branch"])).decode("utf-8")
            except Exception as e:
                log.debug("Could not read existing mapping file %s@%s, starting fresh: %s", path, STATE["source_branch"], e)
                return ""
        files = sssom_service.build(confirmed, author, await _read(SS_PATH), await _read(EQ_PATH), flagged=flagged)
        extra_files = {SS_PATH: files["sssom"].encode("utf-8"),
                       EQ_PATH: files["equiv"].encode("utf-8")}
        map_note = (f"## Reviewed mappings\n\n{files['added']} new "
                    f"{len(confirmed)} positive / {len(flagged)} negative exact-match judgment(s) "
                    f"added to `{SS_PATH}` (SSSOM) and `{EQ_PATH}`.")

    parts = []
    if comment:
        parts.append("**Curator comment:**\n\n" + comment)
    parts.append(f"Submitted via the ARI Metadata Manager by @{u['identity']['login']}.")
    if map_note:
        parts.append(map_note)
    parts.append("## Changes\n\n" + summary)
    pr_body = "\n\n".join(parts)

    return await gh.publish_file(
        token=u["token"], owner=GH_OWNER, repo=GH_REPO, base_branch=STATE["pr_base"],
        path=GH_ONTOLOGY_PATH, content_bytes=content, disease_name=disease,
        message=message, identity=u["identity"], pr_body=pr_body, extra_files=extra_files,
        reuse_branch=reuse_branch, labels=(labels + ["sssom"] if ((confirmed or flagged) and "sssom" not in labels) else labels))


# ----------------------------------------------------------------- SETTINGS / FETCH / EXPORT
def _allowed_branches(branches):
    """Only the working branch and edit/* branches are selectable."""
    return [b for b in branches if b == GH_BASE_BRANCH or b.startswith("edit/")]


async def _fetch_branch(token, branch, login=None):
    data = await gh.get_file_at(token, GH_OWNER, GH_REPO, GH_ONTOLOGY_PATH, branch)
    Path(ONTOLOGY_FILE).write_bytes(data)
    reload_base()
    STATE["source_branch"] = branch
    STATE["pr_base"] = branch          # PR target always matches the source branch
    if login:
        _reset_user(login)


@app.get("/api/v2/settings")
async def get_settings(request: Request):
    u = _user(request)
    token = u["token"] if u else None
    branches = []
    if GH_ENABLED:
        try:
            branches = _allowed_branches(await gh.list_branches(token, GH_OWNER, GH_REPO))
        except Exception as e:
            log.warning("Could not list branches from GitHub: %s", e)
            branches = [GH_BASE_BRANCH]
    return {"github_enabled": GH_ENABLED, "authenticated": bool(u),
            "working_branch": GH_BASE_BRANCH, "source_branch": STATE["source_branch"],
            "pr_base": STATE["pr_base"], "dirty": _dirty(request), "branches": branches}


@app.post("/api/v2/fetch")
async def fetch_changes(request: Request, payload: dict = Body(default={})):
    """Pull the latest of the current source branch into the app."""
    if not GH_ENABLED:
        raise ValueError("GitHub integration is not configured")
    u = _user(request)
    if not u:
        return JSONResponse(status_code=401, content={"detail": "Sign in with GitHub first"})
    if _dirty(request) and not payload.get("discard"):
        return {"needs_confirm": True,
                "detail": "Local edits exist and will be discarded by fetching."}
    await _fetch_branch(u["token"], STATE["source_branch"], u["identity"]["login"])
    return {"ok": True, "source_branch": STATE["source_branch"]}


@app.post("/api/v2/source")
async def set_source(request: Request, payload: dict = Body(...)):
    """Switch which branch the app populates from (working or any edit/* branch)."""
    if not GH_ENABLED:
        raise ValueError("GitHub integration is not configured")
    u = _user(request)
    if not u:
        return JSONResponse(status_code=401, content={"detail": "Sign in with GitHub first"})
    branch = payload.get("branch", "")
    allowed = _allowed_branches(await gh.list_branches(u["token"], GH_OWNER, GH_REPO))
    if branch not in allowed:
        raise ValueError(f"Branch not allowed: {branch}")
    if _dirty(request) and not payload.get("discard"):
        return {"needs_confirm": True,
                "detail": "Local edits exist and will be discarded by switching branch."}
    await _fetch_branch(u["token"], branch, u["identity"]["login"])
    return {"ok": True, "source_branch": branch}


@app.post("/api/v2/pr-base")
async def set_pr_base(request: Request, payload: dict = Body(...)):
    """Set the branch that edits open PRs against."""
    if not GH_ENABLED:
        raise ValueError("GitHub integration is not configured")
    u = _user(request)
    if not u:
        return JSONResponse(status_code=401, content={"detail": "Sign in with GitHub first"})
    branch = payload.get("branch", "")
    allowed = _allowed_branches(await gh.list_branches(u["token"], GH_OWNER, GH_REPO))
    if branch not in allowed:
        raise ValueError(f"Branch not allowed: {branch}")
    STATE["pr_base"] = branch
    return {"ok": True, "pr_base": branch}


@app.get("/api/v2/export")
async def export_excel(request: Request):
    """Export current data to an .xlsx in the core-report format. When signed in,
    changed cells are marked against the source branch (the baseline)."""
    import io as _io, tempfile, os as _os
    baseline = None
    u = _user(request)
    if GH_ENABLED and u:
        tmp_name = None
        try:
            data = await gh.get_file_at(u["token"], GH_OWNER, GH_REPO, GH_ONTOLOGY_PATH, STATE["source_branch"])
            tmp = tempfile.NamedTemporaryFile(suffix=".owl", delete=False)
            tmp.write(data); tmp.close(); tmp_name = tmp.name
            baseline = OntologyService(tmp_name)
        except Exception as e:
            log.warning("Could not load export baseline from source branch %s: %s", STATE["source_branch"], e)
            baseline = None
        finally:
            # Always remove the temp file, even if OntologyService() raised after
            # it was created (owlready2 loads into memory, so the file isn't needed).
            if tmp_name:
                try:
                    _os.unlink(tmp_name)
                except OSError as e:
                    log.debug("Could not remove temp baseline %s: %s", tmp_name, e)
    xlsx = export_service.build_report(service_for(request), baseline)
    return StreamingResponse(
        _io.BytesIO(xlsx),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="ARI_current_changes.xlsx"'})


def _render_html(rel_path: str) -> HTMLResponse:
    """Serve an app HTML page with the `__ASSETV__` asset token substituted.

    The page is marked no-cache so it is always revalidated: it must be fresh to
    carry the current deploy's token, which is what busts the (cacheable) assets."""
    html = (STATIC_DIR / rel_path).read_text(encoding="utf-8")
    return HTMLResponse(html.replace("__ASSETV__", ASSET_VERSION),
                        headers={"Cache-Control": "no-cache, must-revalidate"})


# These HTML routes are registered before the catch-all StaticFiles mount so they
# take precedence and can inject the per-deploy cache-bust token. The mount still
# serves the actual js/css/asset files.
@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
async def _index_html():
    return _render_html("index.html")


@app.get("/ref-edits", include_in_schema=False)
@app.get("/ref-edits/", include_in_schema=False)
@app.get("/ref-edits/index.html", include_in_schema=False)
async def _ref_edits_html():
    return _render_html("ref-edits/index.html")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
