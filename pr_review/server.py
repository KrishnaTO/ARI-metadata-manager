"""Local web app: pick an ARI pull request, see its mapping comparison matrix."""
from __future__ import annotations

import datetime
import threading
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
# The reviewer's own note per row, per PR: {"<pr number>": {row key: {text, updated}}},
# keyed like the marks.
NOTES_PATH = MARKS_PATH.with_name("notes.json")
# Saves are read-modify-write of a whole file and the endpoints run in a thread pool,
# so they are serialized.
_store_lock = threading.Lock()

app = FastAPI(title="ARI PR mapping review")

# PR number -> matrix, rebuilt whenever the PR's head commit moves.
_matrices: dict[int, dict] = {}


class Mark(BaseModel):
    key: str
    marked: bool


class Note(BaseModel):
    key: str
    text: str


def _marks() -> dict[str, list[str]]:
    return atomic_store.read_json(MARKS_PATH, {})


def _notes() -> dict[str, dict[str, dict]]:
    return atomic_store.read_json(NOTES_PATH, {})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/prs")
def prs():
    return github.list_review_prs()


@app.get("/api/prs/{number}")
def matrix(number: int):
    refs = github.pr_refs(number)
    cached = _matrices.get(number)
    if cached is None or cached["pr"]["head_sha"] != refs["head_sha"]:
        cached = _matrices[number] = compare.build_matrix(refs)
    return {**cached, "marks": _marks().get(str(number), []),
            "notes": _notes().get(str(number), {})}


@app.post("/api/prs/{number}/marks")
def set_mark(number: int, mark: Mark):
    with _store_lock:
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


@app.post("/api/prs/{number}/notes")
def set_note(number: int, note: Note):
    """Save the note for one row; blank text deletes it."""
    with _store_lock:
        notes = _notes()
        pr_notes = notes.setdefault(str(number), {})
        text = note.text.strip()
        if text:
            pr_notes[note.key] = {"text": text,
                                  "updated": datetime.datetime.now().isoformat(timespec="seconds")}
        else:
            pr_notes.pop(note.key, None)
        NOTES_PATH.parent.mkdir(exist_ok=True)
        atomic_store.write_json(NOTES_PATH, notes, indent=1)
    return pr_notes
