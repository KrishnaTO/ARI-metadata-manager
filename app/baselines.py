"""The source branch's ontology at a commit, downloaded once.

The publish dialog, the publish itself, sync, resolve and the export each
downloaded the branch's ontology (~1.7 MB) and parsed it; a routine publish
fetched the same bytes three or four times. The bytes at a commit never change,
so they are kept on disk by commit sha (``.user-data/baselines/<sha>.owl``),
the most recently used few, and every caller reads them from there.
"""
import logging
import os
import re

from . import atomic_store, config
from . import github_service as gh

log = logging.getLogger(__name__)

KEEP = 8                                  # ~14 MB at today's file size
_SHA = re.compile(r"[0-9a-f]{40}")


def _dir():
    return config.USER_DIR / "baselines"


async def ontology_at(token: str | None, sha: str) -> bytes:
    """The ontology file's bytes at commit ``sha`` of the configured repo."""
    if not _SHA.fullmatch(sha or ""):
        # It names a file on disk, and resolve takes it from the request body.
        raise ValueError(f"Not a commit sha: {sha!r}")
    path = _dir() / f"{sha}.owl"
    if path.exists():
        os.utime(path)                    # most recently used
        return path.read_bytes()
    data = await gh.get_file_at(token, config.GH_OWNER, config.GH_REPO,
                                config.GH_ONTOLOGY_PATH, sha)
    _dir().mkdir(parents=True, exist_ok=True)
    atomic_store.write_bytes(path, data, mode=0o644)
    _prune()
    return data


def _prune() -> None:
    """Keep the ``KEEP`` most recently used commits."""
    files = sorted(_dir().glob("*.owl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[KEEP:]:
        try:
            old.unlink()
        except OSError as e:
            log.warning("Could not remove cached baseline %s: %s", old.name, e)


async def service_at(token: str | None, sha: str):
    """The ontology at ``sha`` as a service on a private temp copy.

    A private copy because callers write to it (publish grafts onto it and
    saves). The caller deletes ``service.path`` when done."""
    import tempfile

    from .ontology_service import OntologyService
    data = await ontology_at(token, sha)
    tf = tempfile.NamedTemporaryFile(suffix=".owl", delete=False)
    tf.write(data)
    tf.close()
    return OntologyService(tf.name)
