"""Deployment settings, in one place.

Every knob the app reads at startup lives here — environment variables, the
paths derived from them, and the two git-derived version tokens. Other modules
reference these as ``config.NAME`` rather than importing the values, so a test
(or a future reload) can rebind one and have the whole app see it.
"""
import logging
import os
import secrets
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv():
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]          # SESSION_SECRET="abc" must not load the quotes
            os.environ.setdefault(k.strip(), v)


_load_dotenv()

ONTOLOGY_FILE = os.environ.get(
    "ARI_ONTOLOGY_FILE",
    str(ROOT / "ontologies" / "ari_t1d.owl")
)

STATIC_DIR = ROOT / "static"


def _app_version() -> str:
    """Manager version derived from git so it bumps on every update/deploy."""
    try:
        def g(*a):
            return subprocess.check_output(["git", "-C", str(ROOT), *a],
                                           text=True, stderr=subprocess.DEVNULL).strip()

        # README documents `2.<commit-count> (<sha>, <date>)`. The sha was
        # missing, and it is the useful half in a bug report.
        return (f"2.{g('rev-list', '--count', 'HEAD')} "
                f"({g('show', '-s', '--format=%h', 'HEAD')}, "
                f"{g('show', '-s', '--format=%cd', '--date=short', 'HEAD')})")
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("Could not derive app version from git: %s", e)
        return "2.x"


APP_VERSION = _app_version()


def asset_version() -> str:
    """Single cache-busting token for every static asset, changing on each deploy.

    The HTML pages tag all their js/css with `?v=__ASSETV__`, which is replaced
    with this token when the page is served. One value busts every asset at once,
    so there are no fragile per-file `?v=N` numbers to bump (and merge-conflict).

    The token is the newest mtime under ``static/``, and is derived per render.
    Both of those are load-bearing, and both were learned the hard way:

    * **mtime, not the git commit count.** A versioned asset is served
      ``immutable`` for a year, so a token that does not move when a file is
      edited pins the stale copy in every browser that has seen it. The commit
      count does not move while developing, and moves for deploys that touch no
      asset at all. The mtime moves exactly when an asset's bytes do.
    * **Per render, not once at startup.** The HTML is read from disk on every
      request while the token was fixed when the process started, so between
      ``deploy/update.sh`` writing the new files and the restart — which that
      script defers, by design, while a curator is mid-edit — the server handed
      out *new markup stamped with the old token*, and every browser kept
      serving the old, immutable JS against it. Script and markup then disagreed
      about what was on the page, which is exactly the mismatch that made a
      queue write report a DOM error it never had.

    Deriving it walks the ~30 files under ``static/``: cheaper than the fork the
    commit count needed, and it only runs for the two HTML page routes.
    """
    try:
        newest = max((f.stat().st_mtime_ns for f in STATIC_DIR.rglob("*")
                      if f.is_file() and f.suffix in (".js", ".css", ".html")), default=0)
    except OSError as e:
        log.debug("Could not stat static assets for the version token: %s", e)
        newest = 0
    # No asset to date: bust per process rather than serving an empty token.
    return format(newest // 1_000_000_000, "x") if newest else str(int(time.time()))

# ----------------------------------------------------------------- GitHub config
GH_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GH_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
GH_OWNER = os.environ.get("GITHUB_OWNER", "")
GH_REPO = os.environ.get("GITHUB_REPO", "")
GH_BASE_BRANCH = os.environ.get("GITHUB_BASE_BRANCH", "main")
GH_ONTOLOGY_PATH = os.environ.get(
    "GITHUB_ONTOLOGY_PATH", "ontologies/ari_t1d.owl")
MAPPINGS_SSSOM_PATH = os.environ.get("GITHUB_SSSOM_PATH", "mappings/ari.sssom.tsv")
MAPPINGS_EQUIV_PATH = os.environ.get("GITHUB_EQUIV_PATH", "mappings/ari.equivalencies.tsv")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8001").rstrip("/")
OAUTH_CALLBACK_PATH = os.environ.get("OAUTH_CALLBACK_PATH", "/auth/github/callback")
ALLOWED_LOGINS = [s.strip() for s in os.environ.get("ALLOWED_LOGINS", "").split(",") if s.strip()]
REDIRECT_URI = APP_BASE_URL + OAUTH_CALLBACK_PATH
GH_ENABLED = bool(GH_CLIENT_ID and GH_CLIENT_SECRET and GH_OWNER and GH_REPO)

# ----------------------------------------------------------------- sessions
# Tokens are kept SERVER-SIDE (the signed session cookie holds only an opaque id),
# so the GitHub access token never reaches the browser.
SESSIONS_FILE = ROOT / ".sessions.json"
# Sign-ins older than this are swept by the same loop that sweeps working copies.
SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "30"))


def _session_secret() -> str:
    """The cookie signing key.

    A per-process random key signs every curator out on each restart — and the
    deploy timer restarts every ten minutes — while their tokens accumulate in
    the session store forever. So it is required wherever sign-in is enabled.
    """
    secret = os.environ.get("SESSION_SECRET", "")
    if secret:
        return secret
    if GH_ENABLED:
        raise RuntimeError(
            "SESSION_SECRET is required when GitHub sign-in is configured. "
            "Generate one with `python -c \"import secrets; print(secrets.token_hex(32))\"` "
            "and set it in .env — otherwise every restart signs all curators out."
        )
    return secrets.token_hex(32)      # sign-in disabled: local read-only use


# ------------------------------------------------------- per-user working copies
USER_DIR = ROOT / ".user-data"
USER_ARCHIVE_DIR = USER_DIR / "archive"
USER_DATA_TTL_DAYS = int(os.environ.get("USER_DATA_TTL_DAYS", "14"))
# How many curators' ontologies are held in memory at once. Each is a full
# owlready2 World over a ~1.7MB file; the working copies are durable on disk, so
# the cap only costs a reload when an evicted curator comes back.
MAX_LOADED_WORLDS = int(os.environ.get("MAX_LOADED_WORLDS", "8"))

# ----------------------------------------------------------------- review queue
ASSIGN_DIR = ROOT / "assignments"
PROVENANCE_DIR = ROOT / "provenance"
# Curators who may hand work to *other* curators. Empty = anyone signed in (dev
# default). Filling your own queue is never gated — every curator self-assigns.
ASSIGN_ADMINS = [s.strip() for s in os.environ.get("ASSIGN_ADMINS", "").split(",") if s.strip()]
