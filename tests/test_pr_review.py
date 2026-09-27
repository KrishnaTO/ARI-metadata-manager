"""Mapping-PR reviewer: admin gate, and posting a reviewer's notes as one PR review.

GitHub is replaced by a fake reader, so nothing leaves the test."""
from fastapi.testclient import TestClient

import app.main as main
from app import config, sessions
from app.pr_review import github
from app.routes import pr_review

client = TestClient(main.app)

# The real parser, held before any test swaps github.Reader for the fake below.
DIFF_LINES = github.Reader.diff_lines
PATCH = "@@ -10,2 +10,3 @@\n ctx\n-old\n+new\n+added\n"
ROWS = [
    {"key": "ARI:0001001|MONDO|1", "file": github.EQUIV_PATH, "line": 11, "side": "RIGHT",
     "ari_id": "ARI:0001001", "ari_label": "A", "db": "MONDO", "target_id": "1"},
    {"key": "ARI:0001002|DOID|2", "file": github.EQUIV_PATH, "line": 500, "side": "RIGHT",
     "ari_id": "ARI:0001002", "ari_label": "B", "db": "DOID", "target_id": "2"},
]


class FakeReader:
    posted = []

    def __init__(self, token):
        assert token == "tok"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def pr_refs(self, number):
        return {"head_sha": "abc"}

    def diff_lines(self, number, path):
        return _parse(PATCH)

    def create_review(self, number, commit_id, body, comments):
        FakeReader.posted.append({"commit_id": commit_id, "body": body, "comments": comments})
        return {"html_url": "https://github.com/x/pull/7#pullrequestreview-1"}


def _parse(patch):
    class Files:
        repo = "x/y"

        def _json(self, *a, **k):
            return [{"filename": github.EQUIV_PATH, "patch": patch}]
    return DIFF_LINES(Files(), 7, github.EQUIV_PATH)


def test_diff_lines_follows_hunks():
    lines = _parse(PATCH)
    assert lines == {"LEFT": {10, 11}, "RIGHT": {10, 11, 12}}


def test_reviewer_needs_admin(monkeypatch):
    assert client.get("/api/v2/pr-review/prs").status_code == 401
    monkeypatch.setattr(sessions, "_login", lambda request: "curator")
    monkeypatch.setattr(config, "ASSIGN_ADMINS", ["boss"])
    assert client.get("/api/v2/pr-review/prs").status_code == 403


def test_post_review_sends_only_my_unposted_notes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PR_REVIEW_DIR", tmp_path)
    monkeypatch.setattr(config, "ASSIGN_ADMINS", [])
    monkeypatch.setattr(sessions, "_login", lambda request: "me")
    monkeypatch.setattr(sessions, "_user", lambda request: {"token": "tok"})
    monkeypatch.setattr(github, "Reader", FakeReader)
    monkeypatch.setattr(pr_review, "_matrices", {7: {"pr": {"head_sha": "abc"}, "rows": ROWS}})
    FakeReader.posted.clear()

    for key, text in (("ARI:0001001|MONDO|1", "in the diff"), ("ARI:0001002|DOID|2", "outside")):
        assert client.post("/api/v2/pr-review/prs/7/notes", json={"key": key, "text": text}).status_code == 200
    monkeypatch.setattr(sessions, "_login", lambda request: "someone-else")
    client.post("/api/v2/pr-review/prs/7/notes", json={"key": "ARI:0009999|UMLS|C1", "text": "theirs"})
    monkeypatch.setattr(sessions, "_login", lambda request: "me")

    out = client.post("/api/v2/pr-review/prs/7/review")
    assert out.status_code == 200, out.text
    sent = FakeReader.posted[0]
    assert sent["commit_id"] == "abc"
    assert sent["comments"] == [{"path": github.EQUIV_PATH, "line": 11, "side": "RIGHT",
                                 "body": "in the diff"}]
    assert "line 500" in sent["body"] and "outside" in sent["body"] and "theirs" not in sent["body"]
    notes = out.json()["notes"]
    assert notes["ARI:0001001|MONDO|1"]["posted"]["url"].endswith("pullrequestreview-1")
    assert "posted" not in notes["ARI:0009999|UMLS|C1"]

    # Posted notes are not sent twice; editing one makes it postable again.
    assert client.post("/api/v2/pr-review/prs/7/review").status_code == 400
    client.post("/api/v2/pr-review/prs/7/notes", json={"key": "ARI:0001001|MONDO|1", "text": "edited"})
    assert client.post("/api/v2/pr-review/prs/7/review").status_code == 200
    assert FakeReader.posted[1]["comments"][0]["body"] == "edited"
