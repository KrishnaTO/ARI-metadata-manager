"""Curators' marks and notes on report 9 (the synonym vs subtype review).

One shared file, ``config.SYNONYM_REVIEW_DIR/curation.json``:
``{"<ari_id>|<term>": {ari_id, disease, term, verdict, action, review_date,
status, note, by, at}}``. ``ari_id | term`` is unique in the report. Each entry
carries a copy of the row it judges, so the file reads on its own and still
says what was judged after the report is regenerated. ``status`` is one of
``STATUSES`` or empty; a row with neither status nor note has no entry.
Whoever saves last becomes the entry's author.
"""
from __future__ import annotations

import datetime
import threading

from . import atomic_store, config

STATUSES = ("correct", "incorrect", "needs-review")
ROW_FIELDS = ("ari_id", "disease", "term", "verdict", "action", "review_date")

# Saves read-modify-write the whole file from the thread pool, so they are serialized.
_lock = threading.Lock()


def _path():
    return config.SYNONYM_REVIEW_DIR / "curation.json"


def key(row: dict) -> str:
    return f"{row['ari_id']}|{row['term']}"


def read() -> dict:
    return atomic_store.read_json(_path(), {})


def save(row: dict, status: str, note: str, login: str) -> dict | None:
    """Set one row's status and note; both empty removes its entry. Returns the entry."""
    if status and status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)} or empty")
    note = note.strip()
    with _lock:
        state = read()
        if status or note:
            entry = {f: row[f] for f in ROW_FIELDS}
            entry.update(status=status, note=note, by=login,
                         at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"))
            state[key(row)] = entry
        else:
            entry = None
            state.pop(key(row), None)
        config.SYNONYM_REVIEW_DIR.mkdir(exist_ok=True)
        atomic_store.write_json(_path(), state, indent=1)
    return entry
