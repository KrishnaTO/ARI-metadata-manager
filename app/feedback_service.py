"""File-backed feedback log for disease-data terms.

Feedback comments are stored as structured log entries in ``feedback/feedback.json``
(plus an append-only ``feedback/feedback.log`` audit trail). Each entry is tied to a
disease term. By default an entry lives only until the next version release, at which
point it is moved into ``feedback/archive/``; entries flagged ``keep`` survive releases.
"""
import logging
import uuid
from datetime import datetime
from pathlib import Path

from . import atomic_store
from .errors import NotFound

log = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 4000


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _checked_message(message) -> str:
    message = (message or "").strip()
    if not message:
        raise ValueError("Feedback message is empty")
    if len(message) > MAX_MESSAGE_CHARS:
        raise ValueError(f"Feedback message is too long "
                         f"({len(message)} characters; the limit is {MAX_MESSAGE_CHARS})")
    return message


class FeedbackStore:
    def __init__(self, base_dir):
        self.dir = Path(base_dir)
        self.path = self.dir / "feedback.json"
        self.log_path = self.dir / "feedback.log"
        self.archive_dir = self.dir / "archive"

    # ------------------------------------------------------------------ io
    def _ensure_dir(self):
        """Create the store directory on first write, never on construction.

        Every ``OntologyService`` builds one of these from the ontology file's
        grandparent directory, including the throwaway services built over a
        *downloaded* ontology (the publish change summary, the export baseline,
        ``scripts/backfill_id_authors.py``). Those live under the system temp
        directory, whose grandparent is the filesystem root — so creating the
        directory eagerly meant constructing a read-only service tried to mkdir
        ``/feedback`` and raised ``PermissionError`` on Linux."""
        self.dir.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list:
        return atomic_store.read_json(self.path, [])

    def _save(self, items: list):
        self._ensure_dir()
        atomic_store.write_json(self.path, items, indent=2)

    def _log(self, action: str, entry: dict):
        line = (f"{_now()} | {entry.get('author', '')} | {action} | "
                f"term={entry.get('term', '')} | keep={entry.get('keep')} | "
                f"{entry.get('message', '')}\n")
        self._ensure_dir()
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line)

    # ----------------------------------------------------------------- api
    def list(self, disease_iri: str = None) -> list:
        items = self._load()
        if disease_iri:
            items = [x for x in items if x.get("disease_iri") == disease_iri]
        return sorted(items, key=lambda x: x.get("created", ""))

    def add(self, disease_iri: str, term: str, message: str,
            keep: bool = False, author: str = "anonymous") -> dict:
        message = _checked_message(message)
        items = self._load()
        entry = {
            "id": "fb_" + uuid.uuid4().hex[:10],
            "disease_iri": disease_iri,
            "term": term,
            "message": message,
            "keep": bool(keep),
            "author": (author or "anonymous").strip() or "anonymous",
            "created": _now(),
            "updated": _now(),
        }
        items.append(entry)
        self._save(items)
        self._log("ADD", entry)
        return entry

    def update(self, fid: str, message=None, keep=None) -> dict:
        items = self._load()
        found = None
        for x in items:
            if x.get("id") == fid:
                if message is not None:
                    x["message"] = _checked_message(message)
                if keep is not None:
                    x["keep"] = bool(keep)
                x["updated"] = _now()
                found = x
                break
        if found is None:
            raise NotFound(fid)
        self._save(items)
        self._log("EDIT", found)
        return found

    def delete(self, fid: str) -> dict:
        items = self._load()
        removed = next((x for x in items if x.get("id") == fid), None)
        if not removed:
            return {"ok": True, "deleted": False}    # nothing matched; don't rewrite the file
        self._log("DELETE", removed)
        self._save([x for x in items if x.get("id") != fid])
        return {"ok": True, "deleted": True}

    def archive_on_release(self, version: str) -> dict:
        """Move non-kept feedback into a versioned archive; retain flagged entries."""
        items = self._load()
        expiring = [x for x in items if not x.get("keep")]
        retained = [x for x in items if x.get("keep")]
        if expiring:
            self.archive_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = self.archive_dir / f"feedback_v{version}_{ts}.json"
            atomic_store.write_json(dest, expiring, indent=2)
            with open(self.log_path, "a", encoding="utf-8") as f:
                for x in expiring:
                    f.write(f"{_now()} | system | ARCHIVED@v{version} | "
                            f"term={x.get('term', '')} | {x.get('message', '')}\n")
        self._save(retained)
        return {"archived": len(expiring), "retained": len(retained)}
