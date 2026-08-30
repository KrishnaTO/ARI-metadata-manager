"""Publish is not durable until GitHub says so, and a retry is not a second commit.

Covers issue #111 (changes applied before committing; no idempotency key) and
the github_service half of #112 (fork detection, error translation).
"""
import pytest

import app.main as main
from app import github_service as gh


# ------------------------------------------------------------- fork detection
class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


def test_a_same_named_repository_is_not_the_users_fork():
    """A 200 used to be read as 'the fork exists', so a curator who happened to
    own an unrelated repo of the same name got commits pushed into it."""
    unrelated = _Resp(200, {"fork": False, "owner": {"login": "ada"}, "name": "ARI"})
    assert gh._is_fork_of(unrelated, "KrishnaTO", "ARI") is False


def test_a_fork_of_a_different_upstream_is_not_it_either():
    other = _Resp(200, {"fork": True, "parent": {"full_name": "someone-else/ARI"},
                        "owner": {"login": "ada"}, "name": "ARI"})
    assert gh._is_fork_of(other, "KrishnaTO", "ARI") is False


def test_the_real_fork_is_recognised():
    real = _Resp(200, {"fork": True, "parent": {"full_name": "KrishnaTO/ARI"},
                       "owner": {"login": "ada"}, "name": "ARI"})
    assert gh._is_fork_of(real, "KrishnaTO", "ARI") is True
    assert gh._is_fork_of(real, "krishnato", "ari") is True     # GitHub is case-insensitive


def test_a_missing_repository_is_not_a_fork():
    assert gh._is_fork_of(_Resp(404), "KrishnaTO", "ARI") is False


# --------------------------------------------------------- error translation
@pytest.mark.parametrize("status,message,expected", [
    (403, "You have exceeded a secondary rate limit", "rate-limiting"),
    (429, "", "rate-limiting"),
    (401, "Bad credentials", "sign-in has expired"),
    (404, "Not Found", "could not find the repository"),
    (502, "", "having trouble"),
])
def test_github_failures_become_sentences_a_curator_can_act_on(status, message, expected):
    out = gh.explain(status, message)
    assert expected in out
    # Every one of these tells the curator their work is not lost.
    if status != 401:
        assert "saved" in out


def test_an_unrecognised_failure_passes_the_message_through():
    assert gh.explain(422, "Reference already exists") == "Reference already exists"


# ---------------------------------------------------------- idempotency store
@pytest.fixture
def user_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "USER_DIR", tmp_path)
    return tmp_path


def test_a_repeated_publish_returns_the_first_result(user_dir):
    result = {"branch": "edit/ada/x-1", "pr_number": 7, "pr_url": "https://example/7"}
    assert main._remembered_publish("ada", "pub_abc") is None
    main._remember_publish("ada", "pub_abc", result)
    assert main._remembered_publish("ada", "pub_abc") == result
    # A different attempt is not confused with it.
    assert main._remembered_publish("ada", "pub_def") is None
    # Neither is another curator's.
    assert main._remembered_publish("bob", "pub_abc") is None


def test_a_publish_without_a_request_id_is_never_short_circuited(user_dir):
    main._remember_publish("ada", "", {"pr_number": 1})
    assert main._remembered_publish("ada", "") is None
    assert not (user_dir / "ada.publishes.json").exists()


def test_the_replay_guard_is_bounded(user_dir):
    for n in range(main.PUBLISH_KEEP + 10):
        main._remember_publish("ada", f"pub_{n}", {"pr_number": n})
    from app import atomic_store
    kept = atomic_store.read_json(user_dir / "ada.publishes.json", {})
    assert len(kept) == main.PUBLISH_KEEP
    assert f"pub_{main.PUBLISH_KEEP + 9}" in kept       # the newest survives


# ------------------------------------------------------- rollback + touched
def test_a_failed_publish_restores_the_working_copy(user_dir, monkeypatch, base_owl):
    import shutil
    from app.ontology_service import OntologyService
    copy = user_dir / "ada.owl"
    shutil.copy2(base_owl, copy)
    svc = OntologyService(str(copy))
    monkeypatch.setattr(main, "USER_SVC", main.OrderedDict({"ada": svc}))

    snapshot = copy.read_bytes()
    iri = svc.get_diseases_list()[0]["iri"]
    svc.update_disease(iri, {"disease_category": "APPLIED-THEN-FAILED"}, editor="ada")
    assert copy.read_bytes() != snapshot

    main._restore_working_copy("ada", svc, snapshot)

    assert copy.read_bytes() == snapshot
    assert "ada" not in main.USER_SVC       # reloaded from the restored bytes next time


def test_a_successful_publish_stops_scoping_later_pr_bodies(monkeypatch):
    monkeypatch.setattr(main, "USER_TOUCHED", {"ada": {"iri:1", "iri:2"}})
    main._clear_touched("ada")
    assert "ada" not in main.USER_TOUCHED
    main._clear_touched("nobody")           # clearing an absent curator is fine
