"""Redirect safety, security headers, session expiry and .env parsing.

Covers the hardening in issues #110 and #113.
"""
import time

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import config, sessions, workspace
from app.routes import auth as auth_routes

client = TestClient(main.app)


# ------------------------------------------------------------- open redirect
@pytest.mark.parametrize("nxt", [
    "//evil.com",
    "/" + chr(92) + "evil.com",   # browsers normalise the backslash -> protocol-relative
    "\\evil.com",
    "https://evil.com",
    "http:/evil.com",
    "",
])
def test_offsite_redirect_targets_are_refused(nxt):
    assert auth_routes._safe_next(nxt) == "/"


@pytest.mark.parametrize("nxt", [
    "/",
    "/ref-edits/",
    "/#/disease/http%3A%2F%2Fexample.org%2Fd1",
    "/search?q=addison",
])
def test_same_origin_paths_are_kept(nxt):
    assert auth_routes._safe_next(nxt) == nxt


# ---------------------------------------------------------- security headers
def test_responses_carry_the_baseline_headers():
    h = client.get("/api/v2/overview").headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert h["Referrer-Policy"] == "strict-origin-when-cross-origin"
    csp = h["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp
    assert "default-src 'self'" in csp


# --------------------------------------------------------------- session TTL
def test_expired_sessions_are_swept(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SESSIONS_FILE", tmp_path / ".sessions.json")
    monkeypatch.setattr(config, "SESSION_TTL_DAYS", 30)
    fresh = {"token": "t", "identity": {"login": "new"}, "created": time.time()}
    old = {"token": "t", "identity": {"login": "old"}, "created": time.time() - 31 * 86400}
    monkeypatch.setattr(sessions, "SESSIONS", {"a": fresh, "b": old})

    sessions._sweep_sessions()

    assert list(sessions.SESSIONS) == ["a"]


def test_the_session_file_is_created_owner_only(monkeypatch, tmp_path):
    """It holds live GitHub tokens; it must never carry the process umask."""
    import os
    import stat
    path = tmp_path / ".sessions.json"
    monkeypatch.setattr(config, "SESSIONS_FILE", path)
    monkeypatch.setattr(sessions, "SESSIONS", {"a": {"token": "secret"}})

    sessions._save_sessions()

    assert path.exists()
    if os.name != "nt":         # Windows does not model POSIX mode bits
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


# --------------------------------------------------------------- .env values
def test_quoted_env_values_lose_their_quotes(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text('SESSION_SECRET="abc123"\nGITHUB_OWNER=plain\nQUOTED_SINGLE=\'xyz\'\n')
    monkeypatch.setattr(config, "__file__", str(tmp_path / "app" / "config.py"))
    for k in ("SESSION_SECRET", "GITHUB_OWNER", "QUOTED_SINGLE"):
        monkeypatch.delenv(k, raising=False)

    config._load_dotenv()

    import os
    assert os.environ["SESSION_SECRET"] == "abc123"
    assert os.environ["GITHUB_OWNER"] == "plain"
    assert os.environ["QUOTED_SINGLE"] == "xyz"


# ------------------------------------------------------------- OAuth scope
def test_signin_does_not_ask_for_write_access_to_every_repository():
    from app import github_service
    url = github_service.authorize_url("cid", "https://example.org/cb", "state")
    assert "scope=public_repo+user%3Aemail" in url
    assert "scope=repo" not in url


# --------------------------------------------------- merge-regression guard
def test_the_sweep_loop_only_calls_functions_that_exist():
    """A merge once dropped `_sweep_sessions` while leaving its call site.

    Nothing caught it at import time — the background task raises on its first
    tick, six hours after a deploy, in a coroutine nobody is watching. The call
    sites are in `main`'s lifespan and the definitions are in the modules that
    own the state, so this asserts the wiring across that boundary rather than
    the behaviour, which is the part a bad three-way merge silently breaks.
    """
    for mod, name in ((workspace, "_sweep_user_data"),
                      (sessions, "_sweep_sessions"),
                      (sessions, "_save_sessions")):
        assert callable(getattr(mod, name, None)), f"{mod.__name__}.{name} is missing"


def test_the_security_middleware_is_registered():
    """The headers come from a middleware that a merge can quietly delete;
    `test_responses_carry_the_baseline_headers` covers the behaviour, and this
    names the cause so a failure points straight at it."""
    names = {getattr(m.cls, "__name__", "") for m in main.app.user_middleware}
    functions = {getattr(m.kwargs.get("dispatch", None), "__name__", "")
                 for m in main.app.user_middleware}
    assert "security_headers" in (names | functions), (
        "the security_headers middleware is not registered on the app")
