"""FastAPI app for ARI Disease Metadata Manager."""
import os
import json
import logging
import time
import shutil
import asyncio
import secrets
import subprocess
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Body
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .ontology_service import OntologyService
from . import github_service as gh
from . import export_service
from . import diff_service
from . import sssom_service
from . import xref_registry
from . import assignment_service
from . import concept_service
from . import predict_service
from . import id_provenance
from . import atomic_store

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
        # Deliberately NOT fatal, unlike the other stores: a lost session store
        # signs everyone out, which they recover from by signing in again. The
        # provenance, assignment and feedback ledgers hold data nothing can
        # regenerate, so those raise instead (see app/atomic_store.py).
        log.warning("Could not read %s (%s); starting with an empty session store", SESSIONS_FILE.name, e)
        return {}


def _save_sessions():
    try:
        # 0600 at creation, not chmod-after: this file holds live GitHub tokens
        # and between a plain write and the chmod it carries the process umask.
        atomic_store.write_json(SESSIONS_FILE, SESSIONS, mode=0o600)
    except OSError as e:
        log.warning("Could not persist sessions to %s (%s); a restart will sign users out", SESSIONS_FILE.name, e)


# Server-side token store, persisted to disk so a restart (e.g. the auto-update
# timer) does not sign everyone out mid-session.
SESSIONS: dict[str, dict] = _load_sessions()

# Which branch each curator populates FROM and opens pull requests INTO.
#
# This used to be one process-global dict. `POST /api/v2/source` is open to any
# signed-in curator, so one person exploring a colleague's edit/* branch changed
# what every other curator — and every anonymous reader — saw, and left everyone
# else diffing their publish against a baseline that had been swapped underneath
# them. It is per-curator state, so it lives beside their working copy.
def _branch_state_path(login) -> Path:
    return USER_DIR / f"{login}.branch.json"


def _branch_state(login) -> dict:
    """``{"source_branch", "pr_base"}`` for ``login``, defaulting to the base."""
    default = {"source_branch": GH_BASE_BRANCH, "pr_base": GH_BASE_BRANCH}
    if not login:
        return default
    return {**default, **atomic_store.read_json(_branch_state_path(login), {})}


def _set_branch_state(login, **kw):
    if not login:
        return
    atomic_store.write_json(_branch_state_path(login), {**_branch_state(login), **kw})


def _source_branch(request: Request) -> str:
    return _branch_state(_login(request))["source_branch"]


def _pr_base(request: Request) -> str:
    return _branch_state(_login(request))["pr_base"]


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
# How many curators' ontologies are held in memory at once. Each is a full
# owlready2 World over a ~1.7MB file; the working copies are durable on disk, so
# the cap only costs a reload when an evicted curator comes back.
MAX_LOADED_WORLDS = int(os.environ.get("MAX_LOADED_WORLDS", "8"))
USER_SVC: OrderedDict = OrderedDict()             # login -> service, least-recently-used first
USER_DIRTY: set = set()
USER_TOUCHED: dict = {}   # login -> set of disease IRIs actually edited this session


def _login(request: Request):
    u = _user(request)
    return u["identity"]["login"] if u else None


def _adopt_working_copy(login) -> OntologyService:
    """Load ``login``'s working copy into USER_SVC, bounding how many we hold."""
    if len(USER_SVC) >= MAX_LOADED_WORLDS:
        # Each entry is a full owlready2 World; nothing evicted them, so memory
        # grew with every distinct curator until a restart. The copies are
        # durable files, so evicting the least recently used one is free — the
        # next request for it loads it back from disk.
        oldest = next(iter(USER_SVC))
        USER_SVC.pop(oldest, None)
        log.info("Evicted %s's in-memory ontology (cap %d); the working copy on "
                 "disk is untouched", oldest, MAX_LOADED_WORLDS)
    USER_SVC[login] = OntologyService(str(USER_DIR / f"{login}.owl"))
    USER_SVC.move_to_end(login)
    return USER_SVC[login]


def user_service(login, create=False):
    if not login:
        return BASE
    if login in USER_SVC:
        USER_SVC.move_to_end(login)               # most recently used, for the LRU bound
        return USER_SVC[login]
    f = USER_DIR / f"{login}.owl"
    if f.exists():
        # A working copy on disk is this curator's work whether or not the
        # process that made it is still running. Reads must find it too, or an
        # afternoon of unpublished edits appears to have vanished after the
        # ten-minute deploy restart — and the next write used to copy the
        # pristine base straight over the top of it.
        return _adopt_working_copy(login)
    if create:
        USER_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ONTOLOGY_FILE, f)            # snapshot the current base for this user
        os.utime(f, None)                         # copy2 keeps the BASE's mtime, and the
                                                  # sweep reads mtime as "last touched" —
                                                  # a new copy would look idle from birth
        return _adopt_working_copy(login)
    return BASE


def service_for(request: Request, write=False):
    """The ontology a request should read/write: a signed-in user's private copy
    once they have started editing, otherwise the shared base.

    Every write endpoint resolves its ontology through here, so this is where
    write access is gated. An anonymous caller has no private copy, so without
    this guard `user_service(None, create=True)` handed back the shared BASE and
    an unauthenticated request could edit the published ontology directly.
    Where sign-in exists we demand it; a deployment with GitHub integration off
    has no identity to check, so local/offline use still writes to BASE.
    """
    login = _login(request)
    if write:
        if GH_ENABLED and not login:
            raise HTTPException(status_code=401,
                                detail="Sign in with GitHub to edit this ontology")
        return user_service(login, create=True)
    if login and login in USER_SVC:
        return USER_SVC[login]
    return BASE


# ---- review queue: per-curator disease assignments + autosaved decisions
ASSIGN_DIR = Path(__file__).resolve().parent.parent / "assignments"
ASSIGNMENTS = assignment_service.AssignmentStore(ASSIGN_DIR)

# ---- who added each cross-reference id (the review page's separation of duties)
ID_AUTHORS = id_provenance.IdAuthorStore(Path(__file__).resolve().parent.parent / "provenance")

# Curators who may hand work to *other* curators. Empty = anyone signed in (dev
# default). Filling your own queue is never gated — every curator self-assigns.
ASSIGN_ADMINS = [s.strip() for s in os.environ.get("ASSIGN_ADMINS", "").split(",") if s.strip()]


def _require_login(request: Request) -> str:
    login = _login(request)
    if not login:
        raise ValueError("Sign in with GitHub to use your review queue")
    return login


def _can_assign_others(login: str) -> bool:
    return not ASSIGN_ADMINS or login in ASSIGN_ADMINS


def _queue_target(request: Request, target: str) -> str:
    """The curator whose queue a write applies to, defaulting to the caller.

    A curator always manages their own queue; touching someone else's is what
    the ``ASSIGN_ADMINS`` allow-list gates.
    """
    login = _require_login(request)
    target = (target or "").strip() or login
    if target != login and not _can_assign_others(login):
        raise ValueError(f"@{login} may only change their own review queue")
    return target


async def _mapping_judgments(request: Request) -> list:
    """Already-curated positive/negative judgments, from GitHub when signed in."""
    sssom_text = equiv_text = ""
    u = _user(request) if GH_ENABLED else None
    if u:
        async def _read(path):
            try:
                blob = await gh.get_file_at(u["token"], GH_OWNER, GH_REPO, path,
                                            _source_branch(request))
                return blob.decode("utf-8")
            except Exception as e:
                log.debug("Could not read %s@%s from GitHub, falling back to local: %s",
                          path, _source_branch(request), e)
                return ""
        sssom_text = await _read(MAPPINGS_SSSOM_PATH)
        equiv_text = await _read(MAPPINGS_EQUIV_PATH)
    if not sssom_text and not equiv_text:
        root = Path(__file__).resolve().parent.parent
        for p, is_sssom in ((MAPPINGS_SSSOM_PATH, True), (MAPPINGS_EQUIV_PATH, False)):
            try:
                txt = (root / p).read_text(encoding="utf-8")
            except OSError as e:
                log.debug("Could not read local mapping file %s: %s", p, e)
                txt = ""
            if is_sssom:
                sssom_text = txt
            else:
                equiv_text = txt
    return sssom_service.load_judgments(sssom_text, equiv_text)


def _ref_session_path(login) -> Path:
    """Per-user cross-reference review session file (verdicts + PR pointer)."""
    return USER_DIR / f"{login}.refsession.json"


def _load_ref_session(login) -> dict:
    return atomic_store.read_json(_ref_session_path(login), {})


def _save_ref_session(login, data: dict):
    atomic_store.write_json(_ref_session_path(login), data)


def _clear_ref_session(login):
    try:
        _ref_session_path(login).unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("Could not delete cross-ref session for %s: %s", login, e)


def _reset_user(login):
    USER_SVC.pop(login, None)
    USER_DIRTY.discard(login)
    USER_TOUCHED.pop(login, None)
    _clear_ref_session(login)                   # verdicts reference the old data — drop them
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


def _touch(request: Request, *iris):
    """Record disease IRIs this curator actually edited, so the PR summary can
    be scoped to their own changes instead of the whole working copy."""
    login = _login(request)
    if not login:
        return
    USER_TOUCHED.setdefault(login, set()).update(i for i in iris if i)


def _dirty(request: Request):
    login = _login(request)
    return bool(login and login in USER_DIRTY)


USER_ARCHIVE_DIR = USER_DIR / "archive"


def _has_unpublished_work(login: str) -> bool:
    """Whether ``login``'s working copy differs from the published base.

    ``USER_DIRTY`` only knows about the running process, and a restart clears
    it — so a curator on leave over one deploy would look clean. Compare the
    bytes instead: a copy that is byte-identical to the base carries nothing
    that publishing would have produced.
    """
    if login in USER_DIRTY:
        return True
    f = USER_DIR / f"{login}.owl"
    try:
        base = Path(ONTOLOGY_FILE)
        if f.stat().st_size != base.stat().st_size:
            return True
        return f.read_bytes() != base.read_bytes()
    except OSError:
        return True             # can't tell — treat as unpublished and keep it


def _sweep_user_data():
    """Retire per-user working copies idle longer than the TTL.

    A copy with unpublished changes is **archived, never deleted**: this used to
    check the mtime alone, so a curator returning from two weeks' leave found an
    afternoon of unpublished curation gone with no warning and no recovery path.
    A copy identical to the published base carries nothing and is removed.
    """
    if USER_DATA_TTL_DAYS <= 0 or not USER_DIR.exists():
        return
    cutoff = time.time() - USER_DATA_TTL_DAYS * 86400
    for f in USER_DIR.glob("*.owl"):
        try:
            if f.stat().st_mtime >= cutoff:
                continue
            login = f.stem
            if _has_unpublished_work(login):
                USER_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
                dest = USER_ARCHIVE_DIR / f"{login}.{stamp}.owl"
                shutil.move(str(f), str(dest))
                log.warning("Archived @%s's idle working copy with unpublished "
                            "changes to %s (idle > %d days)", login, dest.name,
                            USER_DATA_TTL_DAYS)
            else:
                f.unlink()
            USER_SVC.pop(login, None)
            USER_DIRTY.discard(login)
            _clear_ref_session(login)       # verdicts reference a copy that is no longer live
        except OSError as e:
            log.warning("Could not sweep idle working copy %s: %s", f.name, e)


def working_copy_expiry(login: str) -> dict | None:
    """When ``login``'s working copy is due to be retired, for the UI to surface.

    Returns ``None`` when there is no working copy or the TTL is disabled."""
    if USER_DATA_TTL_DAYS <= 0 or not login:
        return None
    f = USER_DIR / f"{login}.owl"
    if not f.exists():
        return None
    days_left = (f.stat().st_mtime + USER_DATA_TTL_DAYS * 86400 - time.time()) / 86400
    return {
        "days_left": max(0, int(days_left)),
        "ttl_days": USER_DATA_TTL_DAYS,
        "unpublished": _has_unpublished_work(login),
    }


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
    svc = service_for(request, write=True)
    before = svc.get_xrefs(iri)
    r = svc.update_disease(iri, changes, editor=editor)
    # Credit this curator with any cross-reference id the edit introduced, so the
    # review page can stop them confirming their own mapping (separation of duties).
    ID_AUTHORS.record(iri, before, svc.get_xrefs(iri), _login(request))
    _mark_dirty(request)
    _touch(request, iri)
    return r


@app.post("/api/v2/disease/{iri:path}/item")
async def add_item(request: Request, iri: str, payload: dict = Body(...)):
    """Add a data item to a disease. Body: {category, values:{...}, editor}."""
    r = service_for(request, write=True).add_item(iri, payload["category"], payload.get("values", {}),
                         editor=payload.get("editor", "user"))
    _mark_dirty(request)
    _touch(request, iri)
    return r


@app.put("/api/v2/item/{iri:path}")
async def update_item(request: Request, iri: str, payload: dict = Body(...)):
    """Edit a data item. Body: {category, changes:{...}, disease, editor}."""
    r = service_for(request, write=True).update_item(iri, payload["category"], payload.get("changes", {}),
                            disease_iri=payload.get("disease", ""),
                            editor=payload.get("editor", "user"))
    _mark_dirty(request)
    _touch(request, payload.get("disease", ""))
    return r


@app.delete("/api/v2/item/{iri:path}")
async def delete_item(request: Request, iri: str, payload: dict = Body(...)):
    """Delete a data item. Body: {category, disease, editor}."""
    r = service_for(request, write=True).delete_item(iri, payload.get("category", ""),
                            payload["disease"], editor=payload.get("editor", "user"))
    _mark_dirty(request)
    _touch(request, payload.get("disease", ""))
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


@app.get("/api/v2/ref-session")
async def get_ref_session(request: Request):
    """The signed-in user's saved cross-reference review session (verdicts,
    edited-id markers and the PR pointer), so work resumes across page reloads.
    Empty for anonymous users — review state is only persisted when signed in."""
    login = _login(request)
    return _load_ref_session(login) if login else {}


@app.put("/api/v2/ref-session")
async def put_ref_session(request: Request, payload: dict = Body(...)):
    """Persist the signed-in user's cross-reference review session. The body is
    the frontend-owned state blob ({reviewed, edited, published, branch, pr})."""
    login = _login(request)
    if not login:
        return JSONResponse(status_code=401, content={"detail": "Sign in with GitHub first"})
    _save_ref_session(login, payload)
    return {"ok": True}


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
    return await _mapping_judgments(request)


@app.get("/api/v2/id-authors")
async def id_authors():
    """Which curator added each cross-reference id: ``"<iri>|<db>|<id>" -> login``.

    The review page uses this to separate duties — the curator who added an id
    may not also confirm the mapping it stands for."""
    return ID_AUTHORS.authors()


@app.get("/api/v2/predictions")
async def predictions(request: Request):
    """Predicted cross-references for blank review-grid cells (issue #42).

    Exact name/synonym matches against the downloaded reference-database indexes
    (``data/2-databases``); the review page shows these as yellow "predicted" cells
    the curator can verify and confirm. Empty list when no indexes are present."""
    return service_for(request).predict_xrefs()


@app.post("/api/v2/enrichment-preview")
async def enrichment_preview(request: Request, payload: dict = Body(default={})):
    """Synonyms + clinical subtypes a set of confirmed cross-references would add.

    Given a review session's confirmed (positive) mappings
    (``confirmed: [{iri, db, ids}]``), return, per disease iri, the *new*
    ``{synonyms, subtypes}`` the enrichment engine would fold in on publish — the
    matched terms' own synonyms and their direct children. Read-only preview of the
    ``apply_enrichment`` publish step; empty when nothing new is proposed."""
    confirmed = payload.get("confirmed") or []
    return service_for(request).enrichment_preview(confirmed)


@app.get("/api/v2/concept/{db}/{obj_id:path}")
async def concept_detail_lookup(db: str, obj_id: str):
    """Label, synonyms, definition and parents for one target-database id.

    Backs the right-hand side of the reference-review compare pane: the curator sees
    what a candidate concept actually is, next to the ARI disease. ``{obj_id:path}``
    because ids carry colons (and dots, for ICD-10). Distinguishes a database's own
    term (``direct: true``) from a hub cross-reference (``direct: false`` + ``via``);
    a valid-but-unindexed id is a normal ``found: false`` 200, not a 404. Reads the
    public index files only — no auth, same as ``/api/v2/predictions``. An unknown
    ``db`` raises ``KeyError`` -> 404 via the existing handler."""
    return concept_service.lookup(db, obj_id, predict_service.get_indexes())


@app.get("/api/v2/tissues")
async def tissues_list(request: Request):
    """All tissue-target individuals for new-disease creation forms."""
    return service_for(request).get_tissues()


@app.post("/api/v2/disease")
async def create_disease(request: Request, payload: dict = Body(...)):
    """Create a new disease individual. Body: {data: {...}, editor: str}."""
    data = payload.get("data", {})
    editor = payload.get("editor", "user")
    svc = service_for(request, write=True)
    r = svc.create_disease(data, editor=editor)
    ID_AUTHORS.record(r["iri"], {}, svc.get_xrefs(r["iri"]), _login(request))
    _mark_dirty(request)
    _touch(request, r["iri"])
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
            "repo": f"{GH_OWNER}/{GH_REPO}", "base_branch": GH_BASE_BRANCH,
            # Whether this curator may queue work for *other* curators; every
            # signed-in curator can fill their own queue regardless.
            "can_assign_others": _can_assign_others(i["login"])}


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


def _restore_working_copy(login, svc, snapshot: bytes):
    """Put ``login``'s working copy back to ``snapshot`` after a failed publish."""
    try:
        atomic_store.write_bytes(svc.path, snapshot, mode=0o644)
        USER_SVC.pop(login, None)        # reload from the restored bytes on next use
        log.info("Rolled @%s's working copy back after a failed publish", login)
    except OSError as e:
        # Losing the rollback is worse than the original failure, so say so
        # loudly rather than swallowing it.
        log.error("Could not roll @%s's working copy back after a failed publish "
                  "(%s); the changelog entries for this attempt are still applied "
                  "and republishing will repeat them", login, e)


def _clear_touched(login):
    """Forget which diseases this curator has edited, after a successful publish.

    ``USER_TOUCHED`` accumulated every disease IRI a curator touched for the life
    of the process and scoped the change summary in every subsequent PR body, so
    the second and later pull requests of a session described changes that had
    already been published. Only ``_reset_user()`` cleared it."""
    USER_TOUCHED.pop(login, None)


PUBLISH_KEEP = 20        # recent publishes remembered per curator


def _publish_log_path(login) -> Path:
    return USER_DIR / f"{login}.publishes.json"


def _remembered_publish(login, request_id):
    """The result of a publish already completed under ``request_id``.

    If the commit and PR succeed but the *response* is lost — a proxy timeout, a
    closed laptop, a flaky connection — the client shows "Publish failed", the
    curator publishes again, and a second commit lands carrying the same
    judgments. Nothing on either side detected the repeat.
    """
    if not request_id:
        return None
    return atomic_store.read_json(_publish_log_path(login), {}).get(request_id)


def _remember_publish(login, request_id, result):
    if not request_id:
        return
    done = atomic_store.read_json(_publish_log_path(login), {})
    done[request_id] = result
    # Keep the tail; this is a short-window replay guard, not an audit trail.
    if len(done) > PUBLISH_KEEP:
        done = dict(list(done.items())[-PUBLISH_KEEP:])
    atomic_store.write_json(_publish_log_path(login), done)


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
    # Cells judged to have no term at all in the target database.
    absent = payload.get("absent") or []
    author = payload.get("author") or f"github:{u['identity']['login']}"
    any_review = bool(confirmed or flagged or absent)

    # A curator may not confirm a mapping id they added themselves; the frontend
    # blocks it, and this is the boundary check behind that.
    own = ID_AUTHORS.authors()
    login = u["identity"]["login"]
    source_branch = _source_branch(request)     # this curator's baseline, not a global

    # A retry of a publish that actually succeeded returns the original result
    # rather than committing the same judgments twice.
    request_id = (payload.get("request_id") or "").strip()[:64]
    already = _remembered_publish(login, request_id)
    if already is not None:
        log.info("Publish %s by @%s already completed; returning the first result",
                 request_id, login)
        return {**already, "repeated": True}
    for c in confirmed:
        for ident in (c.get("ids") or []):
            if own.get(f"{c.get('iri')}|{c.get('db')}|{ident}") == login:
                return JSONResponse(status_code=400, content={
                    "detail": f"@{login} added {c.get('db')} id {ident} — another curator must confirm it"})

    # Optionally fold each confirmed cross-reference's synonyms and direct children
    # into the disease record itself. Off unless the client opts in, so a plain
    # mappings review never rewrites disease fields.
    apply_enrich = bool(payload.get("apply_enrichment")) and bool(confirmed)

    # Record the review in each affected disease's changelog before snapshotting
    # the ontology for the commit. A write copy is used so the entries land in
    # this user's working ontology, mirroring how field edits are handled.
    svc = service_for(request, write=True) if any_review else service_for(request)
    enrich_note = ""
    # The changelog entries and the enrichment are applied to the working copy
    # *before* the eight GitHub calls that publish it, so any failure among them
    # used to leave the entries applied: the curator saw "Publish failed",
    # clicked again, and the changelog and enrichment were written a second
    # time. Snapshot first and roll back on failure.
    rollback = svc.path.read_bytes() if any_review else None
    if any_review:
        svc.log_xref_review(confirmed, flagged, editor=login, absent=absent)
        if apply_enrich:
            got = svc.apply_enrichment(confirmed, editor=login)
            if got["diseases"]:
                enrich_note = (f"## Enrichment\n\nFolded confirmed cross-references into "
                               f"{got['diseases']} disease(s): +{got['synonyms_added']} "
                               f"synonym(s), +{got['subtypes_added']} clinical subtype(s).")
        _mark_dirty(request)
        _touch(request, *(c.get("iri") for c in confirmed + flagged + absent))
    content = svc.path.read_bytes()

    # Diff current vs the source branch to summarise previous -> new values.
    import tempfile, os as _os
    summary = ""
    tmp_path = None
    try:
        data = await gh.get_file_at(u["token"], GH_OWNER, GH_REPO, GH_ONTOLOGY_PATH, source_branch)
        tf = tempfile.NamedTemporaryFile(suffix=".owl", delete=False)
        tf.write(data); tf.close(); tmp_path = tf.name
        baseline = OntologyService(tmp_path)
        summary = diff_service.build_change_summary(svc, baseline, touched_iris=USER_TOUCHED.get(login))
    except Exception as e:
        log.warning("Could not build change summary against source branch %s: %s", source_branch, e)
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
    if any_review:
        async def _read(path):
            try:
                return (await gh.get_file_at(u["token"], GH_OWNER, GH_REPO, path, source_branch)).decode("utf-8")
            except Exception as e:
                log.debug("Could not read existing mapping file %s@%s, starting fresh: %s", path, source_branch, e)
                return ""
        files = sssom_service.build(confirmed, author, await _read(SS_PATH), await _read(EQ_PATH),
                                    flagged=flagged, absent=absent)
        extra_files = {SS_PATH: files["sssom"].encode("utf-8"),
                       EQ_PATH: files["equiv"].encode("utf-8")}
        map_note = (f"## Reviewed mappings\n\n{files['added']} new "
                    f"{len(confirmed)} positive / {len(flagged)} negative / "
                    f"{len(absent)} no-term-found judgment(s) "
                    f"added to `{SS_PATH}` (SSSOM) and `{EQ_PATH}`.")

    parts = []
    if comment:
        parts.append("**Curator comment:**\n\n" + comment)
    parts.append(f"Submitted via the ARI Metadata Manager by @{u['identity']['login']}.")
    if map_note:
        parts.append(map_note)
    if enrich_note:
        parts.append(enrich_note)
    parts.append("## Changes\n\n" + summary)
    pr_body = "\n\n".join(parts)

    try:
        result = await gh.publish_file(
            token=u["token"], owner=GH_OWNER, repo=GH_REPO, base_branch=_pr_base(request),
            path=GH_ONTOLOGY_PATH, content_bytes=content, disease_name=disease,
            message=message, identity=u["identity"], pr_body=pr_body, extra_files=extra_files,
            reuse_branch=reuse_branch,
            labels=(labels + ["sssom"] if (any_review and "sssom" not in labels) else labels))
    except Exception:
        if rollback is not None:
            _restore_working_copy(login, svc, rollback)
        raise

    _remember_publish(login, request_id, result)
    _clear_touched(login)        # published; later PRs must not re-describe this work
    return result


# ----------------------------------------------------------------- SETTINGS / FETCH / EXPORT
def _allowed_branches(branches):
    """Only the working branch and edit/* branches are selectable."""
    return [b for b in branches if b == GH_BASE_BRANCH or b.startswith("edit/")]


async def _fetch_branch(token, branch, login):
    """Point ``login`` at ``branch``, fetching it into their own working copy.

    This used to write the downloaded bytes over ``ontologies/ari_t1d.owl`` —
    the git-tracked file — so one curator switching branch replaced what every
    other curator and every anonymous reader saw, and left `deploy/update.sh`
    finding a dirty tree (and autostashing) every ten minutes forever.
    """
    data = await gh.get_file_at(token, GH_OWNER, GH_REPO, GH_ONTOLOGY_PATH, branch)
    _reset_user(login)                 # verdicts and edits reference the old base
    USER_DIR.mkdir(parents=True, exist_ok=True)
    atomic_store.write_bytes(USER_DIR / f"{login}.owl", data, mode=0o644)
    USER_SVC.pop(login, None)          # reload on next use, from the file just written
    _set_branch_state(login, source_branch=branch, pr_base=branch)


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
            "working_branch": GH_BASE_BRANCH, "source_branch": _source_branch(request),
            "pr_base": _pr_base(request), "dirty": _dirty(request), "branches": branches,
            "working_copy": working_copy_expiry(_login(request))}


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
    branch = _source_branch(request)
    await _fetch_branch(u["token"], branch, u["identity"]["login"])
    return {"ok": True, "source_branch": branch}


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
    _set_branch_state(_login(request), pr_base=branch)
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
            data = await gh.get_file_at(u["token"], GH_OWNER, GH_REPO, GH_ONTOLOGY_PATH, _source_branch(request))
            tmp = tempfile.NamedTemporaryFile(suffix=".owl", delete=False)
            tmp.write(data); tmp.close(); tmp_name = tmp.name
            baseline = OntologyService(tmp_name)
        except Exception as e:
            log.warning("Could not load export baseline from source branch %s: %s", _source_branch(request), e)
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


# ----------------------------------------------------------------- ASSIGNMENTS
@app.get("/api/v2/assignments")
async def assignments_all(request: Request):
    """Every curator's assignment record — the "Reassign…" / admin view."""
    _require_login(request)
    return ASSIGNMENTS.assignees()


@app.post("/api/v2/assignments")
async def assignments_set(request: Request, payload: dict = Body(...)):
    """Assign diseases. Body: {login?, iris: [...], note?, replace?, reassign?}.

    ``login`` defaults to the caller — self-assignment needs no privilege. A
    disease another curator already holds is refused unless ``reassign``.
    """
    target = _queue_target(request, payload.get("login", ""))
    return ASSIGNMENTS.assign(
        target, payload.get("iris", []),
        note=payload.get("note", ""), replace=bool(payload.get("replace")),
        reassign=bool(payload.get("reassign")))


@app.delete("/api/v2/assignments")
async def assignments_remove(request: Request, payload: dict = Body(...)):
    """Unassign diseases. Body: {login?, iris: [...]}."""
    target = _queue_target(request, payload.get("login", ""))
    return ASSIGNMENTS.unassign(target, payload.get("iris", []))


@app.post("/api/v2/assignments/done")
async def assignments_done(request: Request, payload: dict = Body(...)):
    """Mark one of my diseases finished (or reopen it). Body: {iri, done?}."""
    login = _require_login(request)
    return ASSIGNMENTS.set_done(login, payload.get("iri", ""),
                                done=payload.get("done", True))


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


# The slash-less form redirects rather than renders: the page addresses its own
# assets and sibling pages relatively (the app is mounted under /ari-editor in
# production, so root-absolute paths 404), and relative URLs only resolve correctly
# from the directory form.
@app.get("/ref-edits", include_in_schema=False)
async def _ref_page_slash(request: Request):
    # A relative Location keeps this correct behind the production prefix, which
    # nginx strips before the app sees the path (see deploy/nginx.conf).
    target = request.url.path.rsplit("/", 1)[-1] + "/"
    if request.url.query:
        target += "?" + request.url.query
    return RedirectResponse(target, status_code=308)


@app.get("/ref-edits/", include_in_schema=False)
@app.get("/ref-edits/index.html", include_in_schema=False)
async def _ref_edits_html():
    return _render_html("ref-edits/index.html")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
