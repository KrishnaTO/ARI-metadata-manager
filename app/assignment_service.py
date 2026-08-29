"""Per-curator disease assignments — the review queue behind ``static/ref-edits/``.

One file-backed store lives under ``assignments/`` (gitignored, like ``feedback/``):

  assignments/assignments.json      login -> [disease iri, ...] plus per-disease
                                    "done" flags and an optional note.

A disease belongs to exactly one curator's queue; ``assign`` refuses one another
curator already holds unless the caller passes ``reassign``. Verdicts themselves are
not stored here — the review page keeps them in its own session
(``GET``/``PUT /api/v2/ref-session``) and sends them with ``POST /api/v2/publish``.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

from . import atomic_store

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class AssignmentStore:
    """File-backed per-curator assignments. Safe to construct repeatedly."""

    def __init__(self, base_dir):
        self.dir = Path(base_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "assignments.json"
        self.log_path = self.dir / "assignments.log"

    # ------------------------------------------------------------------ io
    def _read(self, path: Path, fallback):
        return atomic_store.read_json(path, fallback)

    def _write(self, path: Path, data):
        atomic_store.write_json(path, data, indent=2)

    def _audit(self, action: str, login: str, detail: str):
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"{_now()} | {login or '-'} | {action} | {detail}\n")
        except OSError as e:
            log.debug("Could not append to the assignment audit log: %s", e)

    def _load_all(self) -> dict:
        return self._read(self.path, {})

    # --------------------------------------------------------- assignments
    def assignees(self) -> dict:
        """All assignments: login -> {iris: [...], done: [...], note, updated}."""
        return self._load_all()

    def assigned_to(self, login: str) -> dict:
        return self._load_all().get(login, {"iris": [], "done": [], "note": "", "updated": None})

    def owner_of(self, iri: str) -> str | None:
        """Which curator holds this disease; ``None`` when it is unassigned."""
        for login, rec in self._load_all().items():
            if iri in rec.get("iris", []):
                return login
        return None

    def assign(self, login: str, iris: list, note: str = "", replace: bool = False,
               reassign: bool = False) -> dict:
        """Give ``login`` these diseases. Additive unless ``replace``.

        A disease belongs to exactly one curator, so a disease another curator
        already holds is refused unless ``reassign`` — which moves it out of
        their queue (dropping their "done" flag for it) and into this one.
        """
        if not login:
            raise ValueError("A GitHub login is required to assign diseases")
        iris = [str(i) for i in (iris or []) if str(i).strip()]
        data = self._load_all()
        taken = {}
        for other, orec in data.items():
            if other == login:
                continue
            held = [i for i in iris if i in orec.get("iris", [])]
            if held:
                taken[other] = held
        if taken and not reassign:
            who = "; ".join(f"@{o} holds {len(v)}" for o, v in sorted(taken.items()))
            raise ValueError(f"Already assigned to another curator ({who})")
        for other, held in taken.items():
            orec = data[other]
            orec["iris"] = [i for i in orec["iris"] if i not in held]
            orec["done"] = [i for i in orec.get("done", []) if i not in held]
            orec["updated"] = _now()
            self._audit("REASSIGN", other, f"{len(held)} disease(s) moved to @{login}")
        rec = data.setdefault(login, {"iris": [], "done": [], "note": "", "updated": None})
        rec["iris"] = list(iris) if replace else list(dict.fromkeys(rec["iris"] + iris))
        if replace:
            rec["done"] = [i for i in rec.get("done", []) if i in rec["iris"]]
        if note:
            rec["note"] = str(note).strip()
        rec["updated"] = _now()
        self._write(self.path, data)
        self._audit("ASSIGN", login, f"{len(iris)} disease(s){' (replace)' if replace else ''}")
        return rec

    def unassign(self, login: str, iris: list) -> dict:
        drop = set(str(i) for i in (iris or []))
        data = self._load_all()
        rec = data.get(login)
        if not rec:
            raise KeyError(login)
        rec["iris"] = [i for i in rec.get("iris", []) if i not in drop]
        rec["done"] = [i for i in rec.get("done", []) if i not in drop]
        rec["updated"] = _now()
        self._write(self.path, data)
        self._audit("UNASSIGN", login, f"{len(drop)} disease(s)")
        return rec

    def set_done(self, login: str, iri: str, done: bool = True) -> dict:
        """Mark one disease finished (or reopen it) inside a curator's queue."""
        data = self._load_all()
        rec = data.setdefault(login, {"iris": [], "done": [], "note": "", "updated": None})
        if iri not in rec["iris"]:
            rec["iris"].append(iri)
        marked = set(rec.get("done", []))
        marked.add(iri) if done else marked.discard(iri)
        rec["done"] = sorted(marked)
        rec["updated"] = _now()
        self._write(self.path, data)
        self._audit("DONE" if done else "REOPEN", login, iri)
        return rec
