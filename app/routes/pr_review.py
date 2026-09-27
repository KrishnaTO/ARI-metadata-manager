"""Mapping-PR reviewer (``/ref-edits/reviewer/``): curators' mapping PRs on the source
repository, compared row by row against each target database's current term.

Administrators only (``ASSIGN_ADMINS``, as for cutting releases): reviewing other
curators' submissions is a maintainer's job, and each matrix makes a few hundred
lookups against public terminology services from the server's IP. GitHub is read with
the reviewer's own token. Marks and notes are shared by all reviewers of a PR; a reviewer
can post their own notes to the PR as one GitHub review, each on its row's line.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Body, HTTPException, Request

from .. import config, sessions
from ..pr_review import compare, github, store

router = APIRouter()

# PR number -> matrix, rebuilt whenever the PR's head commit moves.
_matrices: dict[int, dict] = {}


def _reviewer(request: Request) -> tuple[str, str]:
    """The signed-in administrator's ``(login, GitHub token)``, else 401/403."""
    login = sessions._require_login(request)
    if not sessions._can_assign_others(login):
        raise HTTPException(status_code=403, detail=(
            f"@{login} is not an administrator; the PR reviewer is for "
            f"{', '.join('@' + a for a in config.ASSIGN_ADMINS)}"))
    return login, sessions._user(request)["token"]


def _upstream(err: httpx.HTTPError) -> HTTPException:
    """A GitHub or terminology-service failure, reported as such rather than as a 500."""
    where = err.request.url.host if err.request else "an upstream service"
    status = f" {err.response.status_code}" if isinstance(err, httpx.HTTPStatusError) else ""
    return HTTPException(status_code=502, detail=f"{where} failed{status}: {err}")


@router.get("/api/v2/pr-review/prs")
def list_prs(request: Request):
    _, token = _reviewer(request)
    try:
        with github.Reader(token) as reader:
            return reader.list_review_prs()
    except httpx.HTTPError as err:
        raise _upstream(err) from err


def _current_matrix(number: int, reader: github.Reader) -> dict:
    refs = reader.pr_refs(number)
    cached = _matrices.get(number)
    if cached is None or cached["pr"]["head_sha"] != refs["head_sha"]:
        cached = _matrices[number] = compare.build_matrix(refs, reader)
    return cached


@router.get("/api/v2/pr-review/prs/{number}")
def matrix(number: int, request: Request):
    login, token = _reviewer(request)
    try:
        with github.Reader(token) as reader:
            cached = _current_matrix(number, reader)
    except httpx.HTTPError as err:
        raise _upstream(err) from err
    return {**cached, **store.read(number), "me": login}


def _field(payload: dict, name: str, kind: type):
    value = payload.get(name)
    if not isinstance(value, kind):
        raise ValueError(f"'{name}' must be a {kind.__name__}")
    return value


# The bodies are plain dicts checked after the admin gate, as elsewhere in the app, so an
# anonymous caller is told to sign in (401) rather than that their body is malformed.
@router.post("/api/v2/pr-review/prs/{number}/marks")
def set_mark(number: int, request: Request, payload: dict = Body(default={})):
    login, _ = _reviewer(request)
    return store.set_mark(number, _field(payload, "key", str), _field(payload, "marked", bool),
                          login)


@router.post("/api/v2/pr-review/prs/{number}/notes")
def set_note(number: int, request: Request, payload: dict = Body(default={})):
    login, _ = _reviewer(request)
    return store.set_note(number, _field(payload, "key", str), _field(payload, "text", str), login)


def _where(row: dict) -> str:
    return (f"`{row['file']}` line {row['line']} — {row['ari_id'] or '(no ARI subject)'} "
            f"{row['ari_label']} → {row['db']} {row['target_id']}")


@router.post("/api/v2/pr-review/prs/{number}/review")
def post_review(number: int, request: Request):
    """Post the caller's unposted notes to the PR as one COMMENT review, as the caller.

    Each note goes on its row's line of the diff. GitHub only accepts comments on lines
    inside the PR's diff hunks, so a note on any other line (a prediction whose key line
    didn't change, say) is listed in the review's body with its line number instead.
    Notes whose row is no longer in the PR are left unposted and reported back.
    """
    login, token = _reviewer(request)
    mine = {k: n for k, n in store.read(number)["notes"].items()
            if n["by"] == login and not n.get("posted")}
    if not mine:
        raise HTTPException(status_code=400, detail=f"@{login} has no unposted notes on #{number}")
    try:
        with github.Reader(token) as reader:
            cached = _current_matrix(number, reader)
            rows = {r["key"]: r for r in cached["rows"]}
            gone = sorted(k for k in mine if k not in rows)
            posting = [rows[k] for k in mine if k in rows]
            if not posting:
                raise HTTPException(status_code=409, detail=(
                    "None of your notes' rows are in the PR any more: " + ", ".join(gone)))
            in_diff = {path: reader.diff_lines(number, path)
                       for path in {r["file"] for r in posting}}
            comments, off_diff = [], []
            for r in posting:
                if r["line"] in in_diff[r["file"]][r["side"]]:
                    comments.append({"path": r["file"], "line": r["line"], "side": r["side"],
                                     "body": mine[r["key"]]["text"]})
                else:
                    off_diff.append(f"- {_where(r)}: {mine[r['key']]['text']}")
            body = f"Mapping review notes from @{login} (ARI metadata manager PR reviewer)."
            if off_diff:
                body += "\n\nOn lines outside this PR's diff:\n" + "\n".join(off_diff)
            review = reader.create_review(number, cached["pr"]["head_sha"], body, comments)
    except httpx.HTTPError as err:
        raise _upstream(err) from err
    state = store.mark_posted(number, [r["key"] for r in posting], review["html_url"])
    return {**state, "review_url": review["html_url"], "on_lines": len(comments),
            "in_body": len(off_diff), "not_posted": gone}
