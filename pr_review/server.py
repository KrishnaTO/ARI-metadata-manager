"""Local web app: pick an ARI pull request, see its equivalency comparison matrix."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import atomic_store

from . import compare, github

STATIC = Path(__file__).resolve().parent / "static"
# Rows the reviewer marked for review, per PR: {"<pr number>": [row key, ...]}. Keyed by
# the row's (ARI id, database, target id) rather than its line, which moves as a PR is
# updated. Local reviewer state, so it lives beside the repo and is gitignored.
MARKS_PATH = Path(__file__).resolve().parent.parent / ".pr-review" / "marks.json"

app = FastAPI(title="ARI PR mapping review")

# PR number -> matrix, rebuilt whenever the PR's head commit moves.
_matrices: dict[int, dict] = {}


class Mark(BaseModel):
    key: str
    marked: bool


def _marks() -> dict[str, list[str]]:
    return atomic_store.read_json(MARKS_PATH, {})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/prs")
def prs():
    return github.list_equivalency_prs()


@app.get("/api/prs/{number}")
def matrix(number: int):
    refs = github.pr_refs(number)
    cached = _matrices.get(number)
    if cached is None or cached["pr"]["head_sha"] != refs["head_sha"]:
        cached = _matrices[number] = compare.build_matrix(refs)
    return {**cached, "marks": _marks().get(str(number), [])}


@app.post("/api/prs/{number}/marks")
def set_mark(number: int, mark: Mark):
    marks = _marks()
    keys = set(marks.get(str(number), []))
    if mark.marked:
        keys.add(mark.key)
    else:
        keys.discard(mark.key)
    marks[str(number)] = sorted(keys)
    MARKS_PATH.parent.mkdir(exist_ok=True)
    atomic_store.write_json(MARKS_PATH, marks, indent=1)
    return marks[str(number)]
