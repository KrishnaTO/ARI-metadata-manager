"""How many GitHub reads the PR reviewer makes (EFFICIENCY.md §9).

Listing the open PRs made one ``/files`` call per PR, opening a cached matrix
still made three calls, and posting a review fetched the file list again.
"""

import httpx
import pytest

from app import config
from app.pr_review import compare, github
from app.routes import pr_review

EQUIV, PRED = github.EQUIV_PATH, github.PREDICTIONS_PATH


@pytest.fixture
def gh(monkeypatch):
    """A fake GitHub behind ``Reader``: records each request's path."""
    monkeypatch.setattr(config, "GH_OWNER", "o")
    monkeypatch.setattr(config, "GH_REPO", "r")
    state = {"calls": [], "files": {7: [{"filename": EQUIV, "patch": "@@ -1,1 +1,2 @@\n a\n+b\n"}]},
             "prs": []}

    def handler(request: httpx.Request):
        state["calls"].append(request.url.path)
        path = request.url.path
        if path == "/graphql":
            nodes = [{"number": n, "title": f"PR {n}", "url": f"https://x/{n}", "createdAt": "2026",
                      "headRefOid": f"sha{n}", "author": {"login": "ada"},
                      "files": {"totalCount": len(fs), "nodes": [{"path": f} for f in fs]}}
                     for n, fs in state["prs"]]
            return httpx.Response(200, json={"data": {"repository": {"pullRequests": {
                "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}})
        if path == "/repos/o/r/pulls/7":
            return httpx.Response(200, json={"head": {"sha": "abc"}, "base": {"sha": "b0", "ref": "main"},
                                             "title": "t", "user": {"login": "ada"},
                                             "html_url": "https://x/7", "state": "open"})
        if path == "/repos/o/r/compare/b0...abc":
            return httpx.Response(200, json={"merge_base_commit": {"sha": "mb"}})
        if path.endswith("/files"):
            number = int(path.split("/")[-2])
            page = int(request.url.params.get("page", 1))
            files = state["files"][number]
            return httpx.Response(200, json=files[(page - 1) * 100: page * 100])
        raise AssertionError(f"unexpected {path}")

    real = httpx.Client
    monkeypatch.setattr(github.httpx, "Client",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return state


def test_listing_open_prs_is_one_call(gh):
    gh["prs"] = [(1, [EQUIV]), (2, ["README.md"]), (3, [PRED, "x.txt"])]
    with github.Reader("tok") as reader:
        prs = reader.list_review_prs()
    assert gh["calls"] == ["/graphql"]
    assert [(p["number"], p["paths"], p["head_sha"]) for p in prs] == [
        (1, [EQUIV], "sha1"), (3, [PRED], "sha3")]


def test_a_cached_matrix_costs_one_call(gh, monkeypatch):
    monkeypatch.setattr(pr_review, "_matrices", {7: {"pr": {"head_sha": "abc"}, "rows": []}})
    with github.Reader("tok") as reader:
        assert pr_review._current_matrix(7, reader)["pr"]["head_sha"] == "abc"
    assert gh["calls"] == ["/repos/o/r/pulls/7"]


def test_the_file_list_is_read_once_per_request(gh, monkeypatch):
    monkeypatch.setattr(compare, "build_matrix", lambda refs, reader: {"pr": refs, "rows": []})
    monkeypatch.setattr(pr_review, "_matrices", {})
    with github.Reader("tok") as reader:
        pr_review._current_matrix(7, reader)
        reader.diff_lines(7, EQUIV)
    assert gh["calls"].count("/repos/o/r/pulls/7/files") == 1
    assert gh["calls"].count("/repos/o/r/pulls/7") == 1


def test_a_pr_with_more_than_100_files_is_read_to_the_end(gh):
    gh["files"][7] = [{"filename": f"f{i}"} for i in range(150)] + [{"filename": EQUIV}]
    with github.Reader("tok") as reader:
        assert reader.pr_refs(7)["paths"] == [EQUIV]


def test_ids_are_grouped_by_disease_once():
    """``_ari_ids`` scanned every equivalencies row for each changed row."""
    head = {(1, "mondo", "1"): {"type": compare.CONFIRMED},
            (1, "doid", "2"): {"type": "manual-negative"},
            (2, "mondo", "3"): {"type": compare.CONFIRMED}}
    by_ari = compare.group_by_ari(head)
    known, rejected = compare._ari_ids(None, by_ari.get(1, []), 1, {(1, "mondo", "1")})
    assert known == {("mondo", "1"): "this PR"}
    assert rejected == {("doid", "2"): "existing"}
