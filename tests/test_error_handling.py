"""The hardened error paths.

An absent state file is a first run and stays quiet. A *present but corrupt*
one is data that existed and is now unreadable: the ledgers raise rather than
read as empty, because continuing on an empty one loses it for good on the next
write. The session store is the one exception — it is regenerable by signing
in again, so it recovers with a warning."""
import logging

from app import config, sessions
from app.feedback_service import FeedbackStore


def _app_warnings(caplog):
    return [r for r in caplog.records
            if r.levelno >= logging.WARNING and r.name.startswith("app")]


def test_load_sessions_missing_file_is_quiet(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(config, "SESSIONS_FILE", tmp_path / "absent.json")
    with caplog.at_level(logging.WARNING):
        assert sessions._load_sessions() == {}
    assert not _app_warnings(caplog)


def test_load_sessions_corrupt_file_warns_and_recovers(tmp_path, monkeypatch, caplog):
    f = tmp_path / "sessions.json"
    f.write_text("{ this is not valid json")
    monkeypatch.setattr(config, "SESSIONS_FILE", f)
    with caplog.at_level(logging.WARNING):
        assert sessions._load_sessions() == {}
    assert any("session" in r.getMessage().lower() for r in _app_warnings(caplog))


def test_feedback_store_refuses_to_read_corrupt_json_as_empty(tmp_path):
    """Present-but-unparseable is not the same as absent.

    Reading it as `[]` meant the next write replaced the damaged file with an
    empty one and the comments were gone for good. It now raises so the bytes
    stay on disk (issue #108)."""
    import pytest

    from app.atomic_store import StoreCorrupt

    store = FeedbackStore(tmp_path / "feedback")
    store.dir.mkdir(parents=True)          # the store only creates this on its first write
    store.path.write_text("{ broken json")
    with pytest.raises(StoreCorrupt):
        store.list()


def test_feedback_store_missing_file_is_quiet(tmp_path, caplog):
    store = FeedbackStore(tmp_path / "feedback")
    with caplog.at_level(logging.WARNING):
        assert store.list() == []
    assert not _app_warnings(caplog)
