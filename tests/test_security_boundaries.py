"""Redirect safety, security headers, session expiry and .env parsing.

Covers the hardening in issues #110 and #113.
"""
import time

import pytest
from fastapi.testclient import TestClient

import app.main as main

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
    assert main._safe_next(nxt) == "/"


@pytest.mark.parametrize("nxt", [
    "/",
    "/ref-edits/",
    "/#/disease/http%3A%2F%2Fexample.org%2Fd1",
    "/search?q=addison",
])
def test_same_origin_paths_are_kept(nxt):
    assert main._safe_next(nxt) == nxt


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
    monkeypatch.setattr(main, "SESSIONS_FILE", tmp_path / ".sessions.json")
    monkeypatch.setattr(main, "SESSION_TTL_DAYS", 30)
    fresh = {"token": "t", "identity": {"login": "new"}, "created": time.time()}
    old = {"token": "t", "identity": {"login": "old"}, "created": time.time() - 31 * 86400}
    monkeypatch.setattr(main, "SESSIONS", {"a": fresh, "b": old})

    main._sweep_sessions()

    assert list(main.SESSIONS) == ["a"]


def test_the_session_file_is_created_owner_only(monkeypatch, tmp_path):
    """It holds live GitHub tokens; it must never carry the process umask."""
    import os
    import stat
    path = tmp_path / ".sessions.json"
    monkeypatch.setattr(main, "SESSIONS_FILE", path)
    monkeypatch.setattr(main, "SESSIONS", {"a": {"token": "secret"}})

    main._save_sessions()

    assert path.exists()
    if os.name != "nt":         # Windows does not model POSIX mode bits
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


# --------------------------------------------------------------- .env values
def test_quoted_env_values_lose_their_quotes(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text('SESSION_SECRET="abc123"\nGITHUB_OWNER=plain\nQUOTED_SINGLE=\'xyz\'\n')
    monkeypatch.setattr(main, "__file__", str(tmp_path / "app" / "main.py"))
    for k in ("SESSION_SECRET", "GITHUB_OWNER", "QUOTED_SINGLE"):
        monkeypatch.delenv(k, raising=False)

    main._load_dotenv()

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
