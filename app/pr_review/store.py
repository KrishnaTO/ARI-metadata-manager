"""Reviewers' shared marks and notes on a mapping PR's rows.

One file per PR (``config.PR_REVIEW_DIR/<number>.json``), shared by every reviewer:
``{"marks": {row key: {by, at}}, "notes": {row key: {text, by, at}}}``. A row key is the
page's ``(judgment,) ARI id | database | target id`` string, not a line number, which
moves as the PR is updated. Each entry records who set it and when; a note has one text,
and whoever saves last replaces it (and becomes its author). A note posted to the PR as a
review comment also records ``posted: {at, url}``; editing it clears that, so the new text
can be posted again.
"""
from __future__ import annotations

import datetime
import threading

from .. import atomic_store, config

# Saves read-modify-write a whole file from the thread pool, so they are serialized.
_lock = threading.Lock()


def _path(number: int):
    return config.PR_REVIEW_DIR / f"{number}.json"


def read(number: int) -> dict:
    state = atomic_store.read_json(_path(number), {})
    return {"marks": state.get("marks", {}), "notes": state.get("notes", {})}


def _stamp(login: str) -> dict:
    return {"by": login, "at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")}


def _write(number: int, state: dict) -> None:
    config.PR_REVIEW_DIR.mkdir(exist_ok=True)
    atomic_store.write_json(_path(number), state, indent=1)


def set_mark(number: int, key: str, marked: bool, login: str) -> dict:
    with _lock:
        state = read(number)
        if marked:
            state["marks"][key] = _stamp(login)
        else:
            state["marks"].pop(key, None)
        _write(number, state)
    return state


def set_note(number: int, key: str, text: str, login: str) -> dict:
    """Save one row's note; blank text deletes it. A saved note is always unposted."""
    with _lock:
        state = read(number)
        if text.strip():
            state["notes"][key] = {"text": text.strip(), **_stamp(login)}
        else:
            state["notes"].pop(key, None)
        _write(number, state)
    return state


def mark_posted(number: int, keys: list[str], url: str) -> dict:
    """Record that the notes on ``keys`` were posted to the PR in the review at ``url``."""
    with _lock:
        state = read(number)
        posted = {"at": _stamp("")["at"], "url": url}
        for key in keys:
            state["notes"][key]["posted"] = posted
        _write(number, state)
    return state
