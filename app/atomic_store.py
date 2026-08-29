"""Atomic file writes and a JSON store that refuses to silently reset.

Every store in this app did a plain ``write_text()`` and every loader caught
``JSONDecodeError`` and returned ``{}``. A half-written file was therefore not
an error anyone saw — it was a silent reset. For ``provenance/`` that meant the
separation-of-duties ledger quietly emptying, letting curators confirm their own
mappings.

Two rules here:

* **Write via a temp file in the same directory, then ``os.replace()``.** The
  rename is atomic on every platform the app runs on, so a reader sees either
  the whole old file or the whole new one, never a truncation.
* **A store that exists but cannot be parsed is fatal.** Missing is fine — that
  is a first run. Present-but-corrupt means data was there and is now
  unreadable, and continuing on an empty one loses it for good on the next
  write.
"""
import json
import os
from pathlib import Path


class StoreCorrupt(RuntimeError):
    """A store file exists but could not be parsed. Never swallow this."""


def write_bytes(path, data: bytes, mode: int | None = None) -> None:
    """Write ``data`` to ``path`` atomically.

    The temp file is created in the destination's own directory so the final
    ``os.replace`` stays within one filesystem, where it is atomic.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        flags = os.O_CREAT | os.O_WRONLY | os.O_TRUNC
        fd = os.open(str(tmp), flags, 0o600 if mode is None else mode)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_text(path, text: str, mode: int | None = None) -> None:
    write_bytes(path, text.encode("utf-8"), mode=mode)


def write_json(path, obj, *, indent=None, mode: int | None = None) -> None:
    write_text(path, json.dumps(obj, indent=indent, ensure_ascii=False), mode=mode)


def read_json(path, default):
    """Parse ``path``, returning ``default`` only when the file is absent.

    A present-but-unparseable file raises ``StoreCorrupt``. Callers that reach
    this at startup should let it stop the process: running on an empty ledger
    is worse than not running.
    """
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise StoreCorrupt(
            f"{path} exists but is not valid JSON ({e}). It holds data this app "
            f"cannot regenerate, so it is not being replaced with an empty one. "
            f"Restore it from a backup, or move it aside to start fresh."
        ) from e
