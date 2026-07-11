"""Shared fixtures for the test suite.

Tests run against a *copy* of the real curated ontology so the write-path tests
can mutate freely without touching the tracked file (and so the on-disk
``releases/`` and ``feedback/`` side directories land under pytest's tmp dir).
"""
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE_OWL = ROOT / "ontologies" / "ari_t1d.owl"


@pytest.fixture(scope="session")
def base_owl() -> Path:
    assert BASE_OWL.exists(), f"ontology fixture missing: {BASE_OWL}"
    return BASE_OWL


def _copy_service(dest_dir: Path, base_owl: Path):
    from app.ontology_service import OntologyService
    onto_dir = dest_dir / "ontologies"
    onto_dir.mkdir(parents=True, exist_ok=True)
    dest = onto_dir / "ari_t1d.owl"
    shutil.copy2(base_owl, dest)
    return OntologyService(str(dest))


@pytest.fixture(scope="session")
def ro_service(tmp_path_factory, base_owl):
    """A shared, read-only service on a temp copy. Do not mutate it."""
    return _copy_service(tmp_path_factory.mktemp("ro"), base_owl)


@pytest.fixture
def make_service(tmp_path, base_owl):
    """Factory that yields independent services on fresh temp copies, so a test
    can build a baseline + a modified copy for diff/export comparisons."""
    counter = {"n": 0}

    def _make():
        counter["n"] += 1
        return _copy_service(tmp_path / f"svc{counter['n']}", base_owl)

    return _make


@pytest.fixture
def service(make_service):
    """A single writable service on a fresh temp copy."""
    return make_service()
