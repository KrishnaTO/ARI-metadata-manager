"""A single server-side allocator for ARI disease numbers.

``_next_ari_number()`` used to scan for the highest existing number and return
max + 1 — but each curator scans their *own private working copy*, so two
curators creating a disease the same afternoon both minted the same number, and
the colliding IRI meant RDF treated two unrelated diseases as one individual.

So numbers come from one counter file instead, held under a process lock and
never reused:

  provenance/ari-id-counter.json   ``{"last": 1213, "log": [...]}``

Seeded once from the ontology's own highest number, and never allowed to move
backwards — a stale working copy cannot hand out a number a merged pull request
has already taken.
"""
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import atomic_store

log = logging.getLogger(__name__)

# One process, one uvicorn worker: a threading lock is the right size. If the
# app ever runs more than one worker this must become an OS-level lock or move
# into SQLite, which provides atomicity and locking together.
_LOCK = threading.Lock()

# How many allocations to keep in the file's audit trail.
_LOG_KEEP = 200


class IdAllocator:
    def __init__(self, base_dir):
        self.path = Path(base_dir) / "ari-id-counter.json"

    def _read(self) -> dict:
        return atomic_store.read_json(self.path, {"last": 0, "log": []})

    def last(self) -> int:
        return int(self._read().get("last", 0))

    def allocate(self, floor: int, editor: str = "") -> int:
        """Reserve and return the next number, never below ``floor``.

        ``floor`` is the highest number the caller can see in its own ontology.
        The counter takes the larger of the two, so a working copy that is ahead
        of the counter (a first run, or a number that arrived via a merge)
        pushes it forward, and one that is behind cannot pull it back.
        """
        with _LOCK:
            data = self._read()
            nxt = max(int(data.get("last", 0)), int(floor)) + 1
            data["last"] = nxt
            entry = {
                "n": nxt,
                "by": editor or "unknown",
                "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "pid": os.getpid(),
            }
            data["log"] = (data.get("log") or [])[-(_LOG_KEEP - 1):] + [entry]
            atomic_store.write_json(self.path, data, indent=2)
            log.info("Allocated ARI number %d to %s", nxt, entry["by"])
            return nxt
