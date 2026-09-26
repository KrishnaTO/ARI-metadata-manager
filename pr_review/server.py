"""Local web app: pick an ARI pull request, see its equivalency comparison matrix."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from . import compare, github

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="ARI PR mapping review")

# PR number -> matrix, rebuilt whenever the PR's head commit moves.
_matrices: dict[int, dict] = {}


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
    return cached
