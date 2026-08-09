"""Per-curator disease assignments and autosaved mapping decisions.

Backs the redesigned reference-review page (``static/ref-edits/``), which works a
curator through *their own* queue of diseases one at a time instead of presenting
the whole 224 x 10 matrix.

Two file-backed stores live under ``assignments/`` (gitignored, like ``feedback/``):

  assignments/assignments.json      login -> [disease iri, ...] plus per-disease
                                    "done" flags and an optional note.
  assignments/decisions/<login>.json  the curator's autosaved per-reference
                                    decisions for the current unpublished session.

Decisions are recorded the moment a curator clicks, so a browser crash never loses
work; publishing is still a separate, explicit step. ``to_publish_payload`` renders
the accumulated decisions into exactly the ``{ari_id, iri, name, db, ids}`` groups
that ``POST /api/v2/publish`` already accepts, so the SSSOM + equivalency writers in
``sssom_service`` are untouched.

Verdicts
  confirm   the id is the same disease  -> published as a positive (skos:exactMatch)
  reject    the id is wrong             -> published as a negative ("Not") mapping
  no_value  no correct id exists in that database at all -> negative, no candidate
  skip      deliberately deferred; never published, but counts as judged so it does
            not hold its database open

A database frequently offers several ids and at most one of them is the disease, so
a row is finished only when every candidate has been judged — see ``disease_panel``.
Confirming an id supersedes any sibling this curator had already confirmed for the
same (disease, database), which keeps that "at most one" true in the published rows.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

VERDICTS = ("confirm", "reject", "no_value", "skip")
# Verdicts that carry a decision through to a published mapping row.
POSITIVE_VERDICTS = ("confirm",)
NEGATIVE_VERDICTS = ("reject", "no_value")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _key(iri: str, db: str, obj_id: str) -> str:
    """One decision per (disease, database, id) — the same key the frontend uses."""
    return f"{iri}|{db}|{obj_id or ''}"


class AssignmentStore:
    """File-backed assignments + decisions. Safe to construct repeatedly."""

    def __init__(self, base_dir):
        self.dir = Path(base_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "assignments.json"
        self.decisions_dir = self.dir / "decisions"
        self.log_path = self.dir / "assignments.log"

    # ------------------------------------------------------------------ io
    def _read(self, path: Path, fallback):
        if not path.exists():
            return fallback
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Could not read %s: %s", path, e)
            return fallback

    def _write(self, path: Path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _audit(self, action: str, login: str, detail: str):
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"{_now()} | {login or '-'} | {action} | {detail}\n")
        except OSError as e:
            log.debug("Could not append to the assignment audit log: %s", e)

    def _load_all(self) -> dict:
        return self._read(self.path, {})

    def _decisions_path(self, login: str) -> Path:
        safe = "".join(c for c in (login or "anonymous") if c.isalnum() or c in "-_.")
        return self.decisions_dir / f"{safe or 'anonymous'}.json"

    def _load_decisions(self, login: str) -> dict:
        return self._read(self._decisions_path(login), {})

    def _save_decisions(self, login: str, data: dict):
        self._write(self._decisions_path(login), data)

    # --------------------------------------------------------- assignments
    def assignees(self) -> dict:
        """All assignments: login -> {iris: [...], done: [...], note, updated}."""
        return self._load_all()

    def assigned_to(self, login: str) -> dict:
        return self._load_all().get(login, {"iris": [], "done": [], "note": "", "updated": None})

    def owner_of(self, iri: str) -> str | None:
        """Which curator holds this disease (first match; assignments are exclusive)."""
        for login, rec in self._load_all().items():
            if iri in rec.get("iris", []):
                return login
        return None

    def assign(self, login: str, iris: list, note: str = "", replace: bool = False) -> dict:
        """Give ``login`` these diseases. Additive unless ``replace``."""
        if not login:
            raise ValueError("A GitHub login is required to assign diseases")
        iris = [str(i) for i in (iris or []) if str(i).strip()]
        data = self._load_all()
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

    # ----------------------------------------------------------- decisions
    def decisions(self, login: str, iri: str = None) -> list:
        items = list(self._load_decisions(login).values())
        if iri:
            items = [d for d in items if d.get("iri") == iri]
        return sorted(items, key=lambda d: d.get("created", ""))

    def decide(self, login: str, iri: str, db: str, obj_id: str, verdict: str,
               name: str = None, ari_id: str = None, label: str = None,
               predicted: bool = False, note: str = "") -> dict:
        """Record (or overwrite) one decision. Idempotent per (iri, db, id)."""
        if verdict not in VERDICTS:
            raise ValueError(f"Unknown verdict {verdict!r}; expected one of {', '.join(VERDICTS)}")
        if not iri or not db:
            raise ValueError("A disease iri and database key are required")
        if verdict != "no_value" and not obj_id:
            raise ValueError("An object id is required for this verdict")
        data = self._load_decisions(login)
        k = _key(iri, db, obj_id)
        prior = data.get(k, {})
        entry = {
            "id": prior.get("id") or "dc_" + uuid.uuid4().hex[:10],
            "key": k,
            "iri": iri,
            "ari_id": ari_id or prior.get("ari_id"),
            "name": name or prior.get("name"),
            "db": db,
            "object_id": obj_id or "",
            "object_label": label if label is not None else prior.get("object_label"),
            "verdict": verdict,
            "predicted": bool(predicted or prior.get("predicted")),
            "note": (note or "").strip() or prior.get("note", ""),
            "author": login,
            "created": prior.get("created") or _now(),
            "updated": _now(),
        }
        # At most one id per (disease, database) can be the match, so confirming one
        # downgrades a sibling this curator had already confirmed to a rejection.
        # Done here rather than in the client so the invariant cannot be lost to a
        # half-finished pair of requests: two exactMatch rows for the same cell would
        # publish a mapping the curator never intended.
        superseded = []
        if verdict == "confirm":
            for other_key, other in data.items():
                if (other_key != k and other.get("iri") == iri and other.get("db") == db
                        and other.get("verdict") == "confirm"):
                    other["verdict"] = "reject"
                    other["updated"] = _now()
                    superseded.append(other.get("object_id"))

        data[k] = entry
        self._save_decisions(login, data)
        self._audit("DECIDE", login, f"{db} {obj_id or '(none)'} -> {verdict} ({iri})")
        if superseded:
            self._audit("SUPERSEDE", login,
                        f"{db} {', '.join(superseded)} -> reject, replaced by {obj_id} ({iri})")
        # ``superseded`` is for the caller's toast only; it is not part of the record.
        return {**entry, "superseded": superseded}

    def undo(self, login: str, decision_id: str) -> dict:
        """Remove one decision — the Undo affordance next to a settled row."""
        data = self._load_decisions(login)
        gone = next((k for k, v in data.items() if v.get("id") == decision_id), None)
        if gone is None:
            raise KeyError(decision_id)
        removed = data.pop(gone)
        self._save_decisions(login, data)
        self._audit("UNDO", login, f"{removed.get('db')} {removed.get('object_id')}")
        return {"ok": True, "removed": removed}

    def clear(self, login: str) -> dict:
        """Drop the whole unpublished decision set (called after a successful publish)."""
        n = len(self._load_decisions(login))
        self._save_decisions(login, {})
        self._audit("CLEAR", login, f"{n} decision(s) published")
        return {"ok": True, "cleared": n}

    # -------------------------------------------------------- publish view
    def to_publish_payload(self, login: str) -> dict:
        """Group decisions into the shape ``POST /api/v2/publish`` already accepts.

        Returns ``{"confirmed": [...], "flagged": [...], "skipped": n}`` where each
        entry is ``{ari_id, iri, name, db, ids}`` — identical to what the old
        per-cell review UI sent, so ``sssom_service`` needs no changes.
        """
        groups: dict = {"confirmed": {}, "flagged": {}}
        skipped = 0
        for d in self.decisions(login):
            verdict = d.get("verdict")
            if verdict == "skip":
                skipped += 1
                continue
            bucket = ("confirmed" if verdict in POSITIVE_VERDICTS
                      else "flagged" if verdict in NEGATIVE_VERDICTS else None)
            if bucket is None:
                continue
            ck = f"{d['iri']}|{d['db']}"
            cell = groups[bucket].setdefault(ck, {
                "ari_id": d.get("ari_id"), "iri": d["iri"],
                "name": d.get("name"), "db": d["db"], "ids": [],
            })
            if d.get("object_id"):
                cell["ids"].append(d["object_id"])
        return {
            "confirmed": [c for c in groups["confirmed"].values() if c["ids"]],
            "flagged": list(groups["flagged"].values()),
            "skipped": skipped,
        }

    def summary(self, login: str) -> dict:
        """Session summary shown before publishing."""
        items = self.decisions(login)
        by_verdict = {v: 0 for v in VERDICTS}
        by_db: dict = {}
        diseases = set()
        for d in items:
            by_verdict[d.get("verdict", "skip")] = by_verdict.get(d.get("verdict", "skip"), 0) + 1
            by_db[d["db"]] = by_db.get(d["db"], 0) + 1
            diseases.add(d["iri"])
        return {
            "total": len(items),
            "by_verdict": by_verdict,
            "by_database": by_db,
            "diseases_touched": len(diseases),
            "predicted_accepted": sum(1 for d in items
                                      if d.get("predicted") and d.get("verdict") == "confirm"),
        }


# --------------------------------------------------------------- queue building
def _index_judgments(judgments: list) -> dict:
    """[{ari_id, prefix, id, judgment}] -> {"ari|prefix|id": judgment}."""
    return {f"{j.get('ari_id')}|{j.get('prefix')}|{j.get('id')}": j.get("judgment")
            for j in (judgments or [])}


def _index_predictions(predictions: list) -> dict:
    """[{ari_id, prefix, id, ...}] -> {"ari|prefix": [prediction, ...]}."""
    out: dict = {}
    for p in (predictions or []):
        out.setdefault(f"{p.get('ari_id')}|{p.get('prefix')}", []).append(p)
    return out


def disease_panel(row: dict, databases: list, judgments: list, predictions: list,
                  decisions: list) -> dict:
    """The payload the disease panel renders: one entry per review database.

    ``row`` is one record from ``OntologyService.get_xref_rows()``; ``databases`` is
    ``xref_registry.public_list()``. Each database entry carries everything the UI
    needs to show a status and offer a single decision — no second round-trip.

    status is one of:
      decided     every candidate has a verdict this session (or "no value" was
                  recorded) — the row is finished and drops out of "remaining"
      partial     some but not all candidates have a verdict; still needs work
      confirmed   a positive judgment already exists in the published mappings
      flagged     a negative judgment already exists
      predicted   the cell is blank but an exact name/synonym candidate exists
      unreviewed  an id exists but has never been judged
      missing     no id and no candidate
    """
    jmap = _index_judgments(judgments)
    pmap = _index_predictions(predictions)
    dmap = {d["key"]: d for d in (decisions or [])}
    ari = row.get("ari_id")
    entries, remaining = [], 0

    for db in databases:
        if not db.get("review"):
            continue
        key, prefix = db["key"], db.get("prefix")
        ids = [i for i in (row.get(key) or []) if i]
        preds = [] if ids else [
            p for p in pmap.get(f"{ari}|{prefix}", [])
            if jmap.get(f"{ari}|{prefix}|{p.get('id')}") != "negative"
        ]

        candidates = []
        for i in ids:
            prior = jmap.get(f"{ari}|{prefix}|{i}")
            decision = dmap.get(_key(row["iri"], key, i))
            candidates.append({
                "id": i, "label": None, "predicted": False,
                "match_field": None, "confidence": None,
                "prior": prior, "decision": decision,
            })
        for p in preds:
            decision = dmap.get(_key(row["iri"], key, p.get("id")))
            candidates.append({
                "id": p.get("id"), "label": p.get("object_label"), "predicted": True,
                "match_field": p.get("match_field"), "confidence": p.get("confidence"),
                "prior": jmap.get(f"{ari}|{prefix}|{p.get('id')}"), "decision": decision,
            })

        no_value = dmap.get(_key(row["iri"], key, ""))
        # A database is finished only when *every* candidate has been judged, not
        # when the first one has: several ids are often offered and only one of them
        # is the disease. Settling on the first decision hid the rest, so a curator
        # who confirmed the wrong id never saw the right one — reviewing multiple
        # sclerosis, SNOMED offers five ids and the first resolves to "diffuse
        # scleroderma". Rejecting all of them is a complete answer too ("none of
        # these"), so it needs no extra click. ``no_value`` settles the row outright.
        decided = [c for c in candidates if c["decision"]]
        settled = bool(no_value) or (bool(candidates) and len(decided) == len(candidates))
        if settled:
            status = "decided"
        elif decided:
            status = "partial"
        elif not candidates:
            status = "missing"
        elif preds:
            status = "predicted"
        elif any(c["prior"] == "positive" for c in candidates):
            status = "confirmed"
        elif any(c["prior"] == "negative" for c in candidates):
            status = "flagged"
        else:
            status = "unreviewed"
        # "Remaining" is what still needs a human decision: anything not settled
        # this session and not already carrying a published judgment.
        if status in ("unreviewed", "predicted", "missing", "partial"):
            remaining += 1

        entries.append({
            "key": key, "label": db["label"], "prefix": prefix,
            "noframe": bool(db.get("noframe")),
            "link": db.get("link"), "search": db.get("search"),
            "status": status, "candidates": candidates,
            "no_value_decision": no_value,
        })

    return {
        "iri": row["iri"], "ari_id": ari, "name": row.get("name"),
        "synonyms": [s for s in (row.get("synonyms") or []) if s],
        "databases": entries,
        "total": len(entries),
        "remaining": remaining,
    }


def queue_for(login: str, store: AssignmentStore, rows: list, databases: list,
              judgments: list, predictions: list) -> dict:
    """The left-hand queue: the curator's diseases with per-disease progress.

    Each item carries a ``coverage`` list — one status per review database, in
    registry order — which is exactly what the 10-square strip in the queue draws.
    """
    rec = store.assigned_to(login)
    assigned, done = rec.get("iris", []), set(rec.get("done", []))
    by_iri = {r["iri"]: r for r in rows}
    decisions = store.decisions(login)

    items, total_refs, total_remaining = [], 0, 0
    for iri in assigned:
        row = by_iri.get(iri)
        if not row:
            log.debug("Assigned disease %s is not in the current ontology; skipping", iri)
            continue
        panel = disease_panel(row, databases, judgments, predictions, decisions)
        total_refs += panel["total"]
        total_remaining += panel["remaining"]
        items.append({
            "iri": iri, "ari_id": panel["ari_id"], "name": panel["name"],
            "total": panel["total"], "remaining": panel["remaining"],
            "done": iri in done,
            "coverage": [d["status"] for d in panel["databases"]],
        })
    # Most work left first, finished diseases last — the order a curator wants.
    items.sort(key=lambda i: (i["done"], -i["remaining"], i["name"] or ""))
    return {
        "login": login,
        "note": rec.get("note", ""),
        "diseases": items,
        "counts": {
            "diseases": len(items),
            "diseases_done": sum(1 for i in items if i["done"]),
            "references": total_refs,
            "remaining": total_remaining,
            "reviewed": total_refs - total_remaining,
        },
        "unpublished": len(decisions),
    }
