"""A working copy keeps the version it started from, and merges the branch into it.

Covers the ancestor files beside each working copy and the sync / resolve /
publish endpoints that merge the source branch in (see
docs/superpowers/specs/2026-09-22-working-copy-merge-design.md).
"""
import shutil
from collections import OrderedDict

import pytest

from app import config, workspace
from app.ontology_service import OntologyService


@pytest.fixture
def curator(tmp_path, monkeypatch, base_owl):
    """A signed-in curator 'ada' with a private user dir and a temp base file."""
    base = tmp_path / "base.owl"
    shutil.copy2(base_owl, base)
    monkeypatch.setattr(config, "USER_DIR", tmp_path / "user")
    monkeypatch.setattr(config, "ONTOLOGY_FILE", str(base))
    monkeypatch.setattr(workspace, "USER_SVC", OrderedDict())
    return "ada"


def _first(svc):
    return svc.get_diseases_list()[0]["iri"]


def test_a_new_working_copy_records_its_ancestor(curator):
    workspace.user_service(curator, create=True)

    assert workspace.ancestor(curator) is not None
    assert workspace.ancestor_sha(curator) is None       # made from the local file
    assert not list(config.USER_DIR.glob("*.base*"))      # never beside the working copies


def test_resetting_a_curator_drops_the_ancestor(curator):
    workspace.user_service(curator, create=True)
    workspace._reset_user(curator)

    assert workspace.ancestor(curator) is None
    assert workspace.ancestor_sha(curator) is None


def test_merge_from_writes_the_working_copy_and_evicts_it(curator, make_service):
    svc = workspace.user_service(curator, create=True)
    d = _first(svc)
    theirs = make_service()
    theirs.update_disease(d, {"definition": "theirs"}, editor="bob")

    out = workspace.merge_from(curator, theirs, [d])

    assert out["merged"] == [d]
    assert curator not in workspace.USER_SVC                  # owlready cache is stale
    assert workspace.user_service(curator).get_disease_detail(d)["definition"] == "theirs"
    assert OntologyService(str(workspace.ancestor_path(curator))) \
        .get_disease_detail(d)["definition"] == "theirs"
