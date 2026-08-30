"""Who is calling, and what they are allowed to do.

Holds the server-side token store and the three questions the routes ask of it:
which session is this, which curator does it belong to, and may that curator act
on someone else's behalf.
"""
import json
import logging
import time

from fastapi import HTTPException, Request

from . import atomic_store, config

log = logging.getLogger(__name__)


def _load_sessions() -> dict:
    if not config.SESSIONS_FILE.exists():
        return {}
    try:
        return json.loads(config.SESSIONS_FILE.read_text())
    except (OSError, json.JSONDecodeError) as e:
        # Deliberately NOT fatal, unlike the other stores: a lost session store
        # signs everyone out, which they recover from by signing in again. The
        # provenance, assignment and feedback ledgers hold data nothing can
        # regenerate, so those raise instead (see app/atomic_store.py).
        log.warning("Could not read %s (%s); starting with an empty session store",
                    config.SESSIONS_FILE.name, e)
        return {}


def _save_sessions():
    try:
        # 0600 at creation, not chmod-after: this file holds live GitHub tokens
        # and between a plain write and the chmod it carries the process umask.
        atomic_store.write_json(config.SESSIONS_FILE, SESSIONS, mode=0o600)
    except OSError as e:
        log.warning("Could not persist sessions to %s (%s); a restart will sign users out",
                    config.SESSIONS_FILE.name, e)


# Server-side token store, persisted to disk so a restart (e.g. the auto-update
# timer) does not sign everyone out mid-session.
SESSIONS: dict[str, dict] = _load_sessions()


def _sweep_sessions():
    """Drop sign-ins older than the TTL.

    Sessions only ever left this store on an explicit logout, so it grew into a
    file of long-lived GitHub tokens for everyone who had ever signed in.
    """
    if config.SESSION_TTL_DAYS <= 0 or not SESSIONS:
        return
    cutoff = time.time() - config.SESSION_TTL_DAYS * 86400
    stale = [sid for sid, v in SESSIONS.items() if v.get("created", 0) < cutoff]
    for sid in stale:
        SESSIONS.pop(sid, None)
    if stale:
        log.info("Swept %d session(s) older than %d days", len(stale), config.SESSION_TTL_DAYS)
        _save_sessions()


def _user(request: Request):
    return SESSIONS.get(request.session.get("sid", ""))


def _login(request: Request):
    u = _user(request)
    return u["identity"]["login"] if u else None


def _require_login(request: Request) -> str:
    """The signed-in curator, or a 401.

    401 rather than a bare ValueError (which the global handler turns into a
    400): "you are not signed in" is a different thing from "your request was
    malformed", and the client shows a sign-in prompt for one and an error for
    the other. Matches ``service_for(write=True)``.
    """
    login = _login(request)
    if not login:
        raise HTTPException(status_code=401, detail="Sign in with GitHub to do this")
    return login


def _can_assign_others(login: str) -> bool:
    return not config.ASSIGN_ADMINS or login in config.ASSIGN_ADMINS
