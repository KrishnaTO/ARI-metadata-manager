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

    def head_sha(self, number):
        return "abc"

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

        def _files(self, number):
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


def test_the_matrix_reads_its_files_together(base_owl, monkeypatch):
    """Five to seven independent file reads per matrix ran one after another
    (EFFICIENCY.md §7)."""
    import threading
    import time

    from app.pr_review import compare

    equiv = "source_prefix\tsource_id\tsource_name\trelation\ttarget_prefix\ttarget_id\ttype\tauthor\n"
    sssom = "subject_id\tobject_id\tcomment\tauthor_id\tmapping_date\n"
    served = {github.EQUIV_PATH: equiv, github.SSSOM_PATH: sssom}
    at_once = {"now": 0, "most": 0}
    lock = threading.Lock()

    class Reader:
        def file_at(self, path, ref):
            with lock:
                at_once["now"] += 1
                at_once["most"] = max(at_once["most"], at_once["now"])
            time.sleep(0.05)
            with lock:
                at_once["now"] -= 1
            return base_owl.read_bytes() if path == config.GH_ONTOLOGY_PATH else served[path].encode()

    monkeypatch.setattr(compare, "get_indexes", lambda: [])
    refs = {"head_sha": "h", "merge_base": "m", "base_ref": "b", "paths": [github.EQUIV_PATH]}
    assert compare.build_matrix(refs, Reader()) == {"pr": refs, "rows": []}
    assert at_once["most"] == 5


def _row(db, ident, judgment, label, synonyms=(), direct=True):
    return {"ari_id": "ARI:0001170", "ari_label": "Progressive hemifacial atrophy",
            "ari_synonyms": ["Parry Romberg syndrome"],
            "db": db, "target_id": ident, "judgment": judgment,
            "status": "added", "found": True, "flags": [], "score": 2,
            "hint": "", "evidence": {"support": [], "against": [], "conflicts": []},
            "views": [{"direct": direct, "label": label, "synonyms": list(synonyms)}]}


def test_a_target_named_like_a_confirmed_sibling_is_credited():
    """Issue #173, from KrishnaTO/ARI#89: DOID:1757 was judged on ARI's names alone,
    while the PR confirmed MONDO:0007710 for the same disease under the same label."""
    from app.pr_review import compare

    mondo = _row("MONDO", "0007710", compare.CONFIRMED, "facial hemiatrophy",
                 ["progressive hemifacial atrophy"])
    doid = _row("DOID", "1757", compare.REJECTED, "facial hemiatrophy", ["PARRY-ROMBERG SYNDROME"])
    snomed = _row("SNOMED", "718224004", compare.CONFIRMED, "facial hemiatrophy", direct=False)
    compare.add_sibling_evidence([mondo, doid, snomed])

    assert doid["evidence"]["siblings"] == [{"db": "MONDO", "id": "0007710",
                                             "name": "facial hemiatrophy"}]
    assert doid["score"] == 4 and doid["hint"] == "Check rejection"
    # A hub term's names are cross-reference evidence already; not counted twice.
    assert snomed["evidence"]["siblings"] == [] and snomed["score"] == 2
    # Only confirmed rows vouch: the rejected DOID row gives MONDO nothing.
    assert mondo["evidence"]["siblings"] == []


def test_a_shared_ari_name_is_not_sibling_evidence():
    # Both targets list ARI's own synonym: that is the name match, not new evidence.
    from app.pr_review import compare

    a = _row("MONDO", "1", compare.CONFIRMED, "x", ["Parry-Romberg syndrome"])
    b = _row("DOID", "2", compare.CONFIRMED, "y", ["PARRY-ROMBERG SYNDROME"])
    compare.add_sibling_evidence([a, b])
    assert a["evidence"]["siblings"] == b["evidence"]["siblings"] == []
