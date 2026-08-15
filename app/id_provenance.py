"""Who added each cross-reference id.

The reference-review page (``static/ref-edits/``) separates duties: the curator
who added a mapping id may not also confirm it. That needs a record of authorship
which outlives the adding curator's own session and working copy, so every edit
introducing a new id for a disease is written to one file-backed ledger:

  provenance/id-authors.json   ``"<disease iri>|<db>|<id>" -> {login, at}``

Only the first author of an id is kept: re-adding an id someone else already
added does not move authorship, so a curator cannot unlock their own mapping by
removing and re-entering it.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def _key(iri: str, db: str, ident: str) -> str:
    return f"{iri}|{db}|{ident}"


class IdAuthorStore:
    """File-backed ledger of which GitHub login added each cross-reference id."""

    def __init__(self, base_dir):
        self.dir = Path(base_dir)
        self.path = self.dir / "id-authors.json"

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Could not read the id-authorship ledger %s: %s", self.path, e)
            return {}

    def authors(self) -> dict:
        """``"<iri>|<db>|<id>" -> login`` for every recorded id."""
        return {k: v["login"] for k, v in self._load().items() if v.get("login")}

    def record(self, iri: str, before: dict, after: dict, login: str) -> int:
        """Credit ``login`` with every id ``after`` has that ``before`` did not.

        ``before`` / ``after`` are ``{db key: [ids]}`` snapshots of one disease's
        cross-references, taken either side of an edit. Returns how many ids were
        newly recorded."""
        if not login:
            return 0
        data = self._load()
        at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        added = 0
        for db, ids in (after or {}).items():
            had = set(str(i) for i in (before or {}).get(db, []))
            for ident in ids:
                k = _key(iri, db, str(ident))
                if str(ident) in had or k in data:
                    continue
                data[k] = {"login": login, "at": at}
                added += 1
        if added:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return added
