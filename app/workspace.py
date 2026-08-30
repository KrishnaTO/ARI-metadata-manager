"""Per-curator working copies of the ontology, and their lifecycle.

Each signed-in curator edits their OWN copy of the ontology; anonymous readers
and offline deployments see the shared ``BASE``. This module owns that copy from
the moment it is created through every edit marker, the branch it was populated
from, the in-progress review session beside it, and the sweep that eventually
retires it.
"""
import logging
import os
import shutil
import time
from collections import OrderedDict
from pathlib import Path

from fastapi import HTTPException, Request

from . import atomic_store, config, sessions
from .ontology_service import OntologyService

log = logging.getLogger(__name__)

BASE = OntologyService(config.ONTOLOGY_FILE)   # shared, source-branch baseline


def reload_base():
    global BASE
    BASE = OntologyService(config.ONTOLOGY_FILE)


USER_SVC: OrderedDict = OrderedDict()   # login -> service, least-recently-used first
USER_DIRTY: set = set()
USER_TOUCHED: dict = {}   # login -> set of disease IRIs actually edited this session


# Which branch each curator populates FROM and opens pull requests INTO.
#
# This used to be one process-global dict. `POST /api/v2/source` is open to any
# signed-in curator, so one person exploring a colleague's edit/* branch changed
# what every other curator — and every anonymous reader — saw, and left everyone
# else diffing their publish against a baseline that had been swapped underneath
# them. It is per-curator state, so it lives beside their working copy.
def _branch_state_path(login) -> Path:
    return config.USER_DIR / f"{login}.branch.json"


def _branch_state(login) -> dict:
    """``{"source_branch", "pr_base"}`` for ``login``, defaulting to the base."""
    default = {"source_branch": config.GH_BASE_BRANCH, "pr_base": config.GH_BASE_BRANCH}
    if not login:
        return default
    return {**default, **atomic_store.read_json(_branch_state_path(login), {})}


def _set_branch_state(login, **kw):
    if not login:
        return
    atomic_store.write_json(_branch_state_path(login), {**_branch_state(login), **kw})


def _source_branch(request: Request) -> str:
    return _branch_state(sessions._login(request))["source_branch"]


def _pr_base(request: Request) -> str:
    return _branch_state(sessions._login(request))["pr_base"]


def _adopt_working_copy(login) -> OntologyService:
    """Load ``login``'s working copy into USER_SVC, bounding how many we hold."""
    if len(USER_SVC) >= config.MAX_LOADED_WORLDS:
        # Each entry is a full owlready2 World; nothing evicted them, so memory
        # grew with every distinct curator until a restart. The copies are
        # durable files, so evicting the least recently used one is free — the
        # next request for it loads it back from disk.
        oldest = next(iter(USER_SVC))
        USER_SVC.pop(oldest, None)
        log.info("Evicted %s's in-memory ontology (cap %d); the working copy on "
                 "disk is untouched", oldest, config.MAX_LOADED_WORLDS)
    USER_SVC[login] = OntologyService(str(config.USER_DIR / f"{login}.owl"))
    USER_SVC.move_to_end(login)
    return USER_SVC[login]


def user_service(login, create=False):
    if not login:
        return BASE
    if login in USER_SVC:
        USER_SVC.move_to_end(login)               # most recently used, for the LRU bound
        return USER_SVC[login]
    f = config.USER_DIR / f"{login}.owl"
    if f.exists():
        # A working copy on disk is this curator's work whether or not the
        # process that made it is still running. Reads must find it too, or an
        # afternoon of unpublished edits appears to have vanished after the
        # ten-minute deploy restart — and the next write used to copy the
        # pristine base straight over the top of it.
        return _adopt_working_copy(login)
    if create:
        config.USER_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config.ONTOLOGY_FILE, f)     # snapshot the current base for this user
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
    login = sessions._login(request)
    if write:
        if config.GH_ENABLED and not login:
            raise HTTPException(status_code=401,
                                detail="Sign in with GitHub to edit this ontology")
        return user_service(login, create=True)
    if login and login in USER_SVC:
        return USER_SVC[login]
    return BASE


# ------------------------------------------------- in-progress review session
def _ref_session_path(login) -> Path:
    """Per-user cross-reference review session file (verdicts + PR pointer)."""
    return config.USER_DIR / f"{login}.refsession.json"


def _load_ref_session(login) -> dict:
    return atomic_store.read_json(_ref_session_path(login), {})


def _save_ref_session(login, data: dict):
    atomic_store.write_json(_ref_session_path(login), data)


# The three per-key maps in a review session. Every entry is one cell or one id,
# so two windows editing different cells merge with no ambiguity at all.
REF_SESSION_MAPS = ("reviewed", "edited", "published")


def _merge_ref_session(login, patch: dict, branch, pr) -> dict:
    """Fold one window's changes into the stored session.

    The page used to PUT the *entire* state blob every 500ms and this wrote it
    wholesale, so the last window to save replaced the whole document and any
    verdict recorded in the other one vanished on the next reload (issue #114).
    A patch carries only the keys that changed; ``None`` means the curator
    cleared that verdict, which is the one thing a plain merge cannot express.

    ``branch`` and ``pr`` are only ever set, never cleared: publishing is what
    assigns them, and a background save from a window that loaded before the
    publish must not reset them to null.
    """
    cur = _load_ref_session(login)
    for name in REF_SESSION_MAPS:
        target = dict(cur.get(name) or {})
        for key, value in (patch.get(name) or {}).items():
            if value is None:
                target.pop(key, None)
            else:
                target[key] = value
        cur[name] = target
    if branch:
        cur["branch"] = branch
    if pr:
        cur["pr"] = pr
    _save_ref_session(login, cur)
    return cur


def _clear_ref_session(login):
    try:
        _ref_session_path(login).unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("Could not delete cross-ref session for %s: %s", login, e)


# ------------------------------------------------------------- edit markers
def _reset_user(login):
    USER_SVC.pop(login, None)
    USER_DIRTY.discard(login)
    USER_TOUCHED.pop(login, None)
    _clear_ref_session(login)                   # verdicts reference the old data — drop them
    try:
        (config.USER_DIR / f"{login}.owl").unlink()
    except FileNotFoundError:
        pass                                    # never edited — nothing to reset
    except OSError as e:
        log.warning("Could not delete working copy for %s: %s", login, e)


def _mark_dirty(request: Request):
    login = sessions._login(request)
    if login:
        USER_DIRTY.add(login)


def _touch(request: Request, *iris):
    """Record disease IRIs this curator actually edited, so the PR summary can
    be scoped to their own changes instead of the whole working copy."""
    login = sessions._login(request)
    if not login:
        return
    USER_TOUCHED.setdefault(login, set()).update(i for i in iris if i)


def _dirty(request: Request):
    login = sessions._login(request)
    return bool(login and login in USER_DIRTY)


def _clear_touched(login):
    """Forget which diseases this curator has edited, after a successful publish.

    ``USER_TOUCHED`` accumulated every disease IRI a curator touched for the life
    of the process and scoped the change summary in every subsequent PR body, so
    the second and later pull requests of a session described changes that had
    already been published. Only ``_reset_user()`` cleared it."""
    USER_TOUCHED.pop(login, None)


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


# ----------------------------------------------------------------- retirement
def _has_unpublished_work(login: str) -> bool:
    """Whether ``login``'s working copy differs from the published base.

    ``USER_DIRTY`` only knows about the running process, and a restart clears
    it — so a curator on leave over one deploy would look clean. Compare the
    bytes instead: a copy that is byte-identical to the base carries nothing
    that publishing would have produced.
    """
    if login in USER_DIRTY:
        return True
    f = config.USER_DIR / f"{login}.owl"
    try:
        base = Path(config.ONTOLOGY_FILE)
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
    if config.USER_DATA_TTL_DAYS <= 0 or not config.USER_DIR.exists():
        return
    cutoff = time.time() - config.USER_DATA_TTL_DAYS * 86400
    for f in config.USER_DIR.glob("*.owl"):
        try:
            if f.stat().st_mtime >= cutoff:
                continue
            login = f.stem
            if _has_unpublished_work(login):
                config.USER_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
                dest = config.USER_ARCHIVE_DIR / f"{login}.{stamp}.owl"
                shutil.move(str(f), str(dest))
                log.warning("Archived @%s's idle working copy with unpublished "
                            "changes to %s (idle > %d days)", login, dest.name,
                            config.USER_DATA_TTL_DAYS)
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
    if config.USER_DATA_TTL_DAYS <= 0 or not login:
        return None
    f = config.USER_DIR / f"{login}.owl"
    if not f.exists():
        return None
    days_left = (f.stat().st_mtime + config.USER_DATA_TTL_DAYS * 86400 - time.time()) / 86400
    return {
        "days_left": max(0, int(days_left)),
        "ttl_days": config.USER_DATA_TTL_DAYS,
        "unpublished": _has_unpublished_work(login),
    }
