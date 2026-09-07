"""Cross-reference ids a curator edited off a disease record.

Flagging an id on the reference-review page records a decision: the publish
sends it in ``flagged``, which both drops the id from the ontology *and* writes
the negative SSSOM row that says who ruled against it. Editing the same id out
of the disease record went nowhere near that path — ``update_disease`` and
``apply_xref_op`` write the ontology and nothing else — so a curated id could
leave the registry with no judgment behind it and no author against it.

That is not a theoretical gap. ARI:0001158 lost four ids that way (DOID
0060234, umls C1275078, ncit C98873, mesh C563187) half an hour before its
review was submitted, and the data repo's ``xref-deleted`` rule rejected the
pull request, which is how it was found.

So every removal is parked here as a pending negative judgment:

  provenance/xref-removals.json   ``"<disease iri>|<db>|<id>" -> {login, at}``

and the next publish folds them into ``flagged`` alongside the review page's
own. Both routes then produce one kind of record through one code path, rather
than the export having to learn where a removal came from.

An entry leaves the ledger two ways: the id comes back before publishing — a
removal undone was never a judgment — or the publish carrying it succeeds.

Only well-formed ids are parked. The data repo requires a negative row for a
removed id *if and only if* it is well-formed, because a row carrying a
malformed id is itself rejected; dropping an ICD-9 code stored under ICD-10 is
a repair, not a ruling. ``xref_registry.well_formed`` draws that line.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

from . import atomic_store
from .xref_registry import normalize_id, well_formed

log = logging.getLogger(__name__)


def _key(iri: str, db: str, ident: str) -> str:
    return f"{iri}|{db}|{ident}"


class XrefRemovalStore:
    """File-backed ledger of removals awaiting publication as judgments."""

    def __init__(self, base_dir):
        self.dir = Path(base_dir)
        self.path = self.dir / "xref-removals.json"

    def _load(self) -> dict:
        """The ledger, or ``{}`` on a first run.

        A corrupt ledger raises rather than reading as empty: these are curation
        decisions that have not been published yet, and reading them as absent
        would drop them silently — the exact failure this module exists to stop.
        """
        return atomic_store.read_json(self.path, {})

    def record(self, iri: str, before: dict, after: dict, login: str) -> int:
        """Park every id ``before`` held that ``after`` does not, and release any
        that came back.

        ``before`` / ``after`` are ``{db key: [ids]}`` snapshots of one disease's
        cross-references, taken either side of an edit — the same pair the
        authorship ledger diffs in the other direction. Returns how many entries
        changed.
        """
        data = self._load()
        at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        changed = 0
        for db in set(before or {}) | set(after or {}):
            had = {i for i in (normalize_id(db, x) for x in (before or {}).get(db, [])) if i}
            now = {i for i in (normalize_id(db, x) for x in (after or {}).get(db, [])) if i}
            for ident in had - now:
                key = _key(iri, db, ident)
                if key in data or not well_formed(db, ident):
                    continue
                data[key] = {"login": login or "", "at": at}
                changed += 1
                log.info("Parked %s id %s removed from %s by %s", db, ident, iri, login or "anon")
            for ident in now - had:
                if data.pop(_key(iri, db, ident), None) is not None:
                    changed += 1
        if changed:
            atomic_store.write_json(self.path, data, indent=2)
        return changed

    def pending(self, iris=None) -> list:
        """Parked removals as ``flagged`` groups: ``[{iri, db, ids}]``.

        ``iris`` limits the result to the diseases a publish is speaking for, so
        a removal on a disease outside its scope stays parked for the publish
        that does cover it.
        """
        groups: dict = {}
        for key in self._load():
            iri, db, ident = key.rsplit("|", 2)
            if iris is not None and iri not in iris:
                continue
            groups.setdefault((iri, db), []).append(ident)
        return [{"iri": iri, "db": db, "ids": ids} for (iri, db), ids in groups.items()]

    def clear(self, groups) -> int:
        """Drop the parked entries named by ``flagged``-shaped ``groups``.

        Called once a publish carrying them has landed. Groups the review page
        contributed are not in the ledger and pass through harmlessly, so the
        caller can hand over the whole ``flagged`` list without sorting it.
        """
        data = self._load()
        dropped = 0
        for group in groups or []:
            for ident in (group.get("ids") or []):
                key = _key(group.get("iri", ""), group.get("db", ""),
                           normalize_id(group.get("db", ""), ident))
                if data.pop(key, None) is not None:
                    dropped += 1
        if dropped:
            atomic_store.write_json(self.path, data, indent=2)
        return dropped
