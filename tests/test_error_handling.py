"""The hardened error paths: corrupt state files must recover *and* leave a trail
(a warning), while the normal 'file absent' case stays quiet."""
import logging

import app.main as main
from app.feedback_service import FeedbackStore


def _app_warnings(caplog):
    return [r for r in caplog.records
            if r.levelno >= logging.WARNING and r.name.startswith("app")]


def test_load_sessions_missing_file_is_quiet(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(main, "SESSIONS_FILE", tmp_path / "absent.json")
    with caplog.at_level(logging.WARNING):
        assert main._load_sessions() == {}
    assert not _app_warnings(caplog)


def test_load_sessions_corrupt_file_warns_and_recovers(tmp_path, monkeypatch, caplog):
    f = tmp_path / "sessions.json"
    f.write_text("{ this is not valid json")
    monkeypatch.setattr(main, "SESSIONS_FILE", f)
    with caplog.at_level(logging.WARNING):
        assert main._load_sessions() == {}
    assert any("session" in r.getMessage().lower() for r in _app_warnings(caplog))


def test_feedback_store_survives_corrupt_json(tmp_path, caplog):
    store = FeedbackStore(tmp_path / "feedback")
    store.path.write_text("{ broken json")
    with caplog.at_level(logging.WARNING):
        assert store.list() == []
    assert any("feedback" in r.getMessage().lower() for r in _app_warnings(caplog))


def test_feedback_store_missing_file_is_quiet(tmp_path, caplog):
    store = FeedbackStore(tmp_path / "feedback")
    with caplog.at_level(logging.WARNING):
        assert store.list() == []
    assert not _app_warnings(caplog)
