"""GitHub sign-in, and who the caller is.

The OAuth round trip and the endpoints the frontend uses to decide what to show:
the signed-in identity, and the repository's open pull requests.
"""
import logging
import secrets
import time
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .. import config, sessions
from .. import github_service as gh

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/v2/open-prs")
async def open_prs(request: Request):
    """Open pull requests in the repo, so the disease page can surface unmerged /
    unreviewed changes (issue #19). Works unauthenticated for a public repo; a
    signed-in user's token is used when available (e.g. private repos, rate limit).
    The frontend matches a PR to a disease via its `edit/<login>/<slug>-<ts>` branch."""
    if not config.GH_ENABLED:
        return {"github_enabled": False, "prs": []}
    u = sessions._user(request)
    token = u["token"] if u else None
    try:
        prs = await gh.list_open_prs(token, config.GH_OWNER, config.GH_REPO)
    except Exception:
        prs = []
    return {"github_enabled": True, "prs": prs}


@router.get("/api/v2/me")
async def me(request: Request):
    if not config.GH_ENABLED:
        return {"github_enabled": False, "authenticated": False}
    u = sessions._user(request)
    if not u:
        return {"github_enabled": True, "authenticated": False}
    i = u["identity"]
    return {"github_enabled": True, "authenticated": True,
            "login": i["login"], "name": i["name"], "avatar": i["avatar"],
            "repo": f"{config.GH_OWNER}/{config.GH_REPO}", "base_branch": config.GH_BASE_BRANCH,
            # Whether this curator may queue work for *other* curators; every
            # signed-in curator can fill their own queue regardless.
            "can_assign_others": sessions._can_assign_others(i["login"])}


def _safe_next(nxt: str) -> str:
    """Only allow same-origin relative paths (avoid open redirects).

    Prefix-matching on "/" is not enough: browsers normalise backslashes to
    forward slashes, so a backslash-prefixed path reads as a protocol-relative
    URL and leaves the site. Parse it and require an empty scheme and netloc.
    """
    if not nxt or "\\" in nxt or not nxt.startswith("/"):
        return "/"
    parts = urlsplit(nxt)
    if parts.scheme or parts.netloc:
        return "/"
    return urlunsplit(("", "", parts.path, parts.query, parts.fragment)) or "/"


@router.get("/auth/github")
async def auth_github(request: Request, next: str = "/"):
    if not config.GH_ENABLED:
        return JSONResponse(status_code=404, content={"detail": "GitHub integration not configured"})
    st = secrets.token_hex(16)
    request.session["oauth_state"] = st
    request.session["oauth_next"] = _safe_next(next)
    return RedirectResponse(gh.authorize_url(config.GH_CLIENT_ID, config.REDIRECT_URI, st))


@router.get(config.OAUTH_CALLBACK_PATH)
async def auth_callback(request: Request, code: str = "", state: str = ""):
    if not config.GH_ENABLED:
        return JSONResponse(status_code=404, content={"detail": "GitHub integration not configured"})
    if not code or state != request.session.get("oauth_state"):
        return JSONResponse(status_code=400, content={"detail": "Invalid OAuth state"})
    token = await gh.exchange_code(config.GH_CLIENT_ID, config.GH_CLIENT_SECRET, code, config.REDIRECT_URI)
    identity = await gh.get_identity(token)
    if config.ALLOWED_LOGINS and identity["login"] not in config.ALLOWED_LOGINS:
        return JSONResponse(status_code=403, content={"detail": f"@{identity['login']} is not allowed"})
    if not config.ALLOWED_LOGINS:
        # No allow-list means anyone with repo access can sign in. That is the
        # dev default; in production it should at least be visible in the log.
        log.warning("Sign-in by @%s with ALLOWED_LOGINS unset — any GitHub user "
                    "with repo access can sign in", identity["login"])
    sid = secrets.token_urlsafe(24)
    sessions.SESSIONS[sid] = {"token": token, "identity": identity, "created": time.time()}
    sessions._save_sessions()
    request.session["sid"] = sid
    request.session.pop("oauth_state", None)
    return RedirectResponse(_safe_next(request.session.pop("oauth_next", "/")))


@router.post("/api/v2/logout")
async def logout(request: Request):
    sessions.SESSIONS.pop(request.session.pop("sid", ""), None)
    sessions._save_sessions()
    return {"ok": True}
