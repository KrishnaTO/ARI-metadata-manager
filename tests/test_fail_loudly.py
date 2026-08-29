"""Failures that used to report success.

Covers issue #115 (invalid values dropped silently, the changelog no-op, and
exception handlers that turned bugs into plausible client errors) and the parts
of #123 that are testable.
"""
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.errors import Invalid, NotFound

client = TestClient(main.app)


# --------------------------------------------------- rejected field values
def test_a_value_that_cannot_be_stored_is_reported(service):
    """A numeric field given prose saved 'successfully', wrote a changelog entry
    and stored nothing — a data-entry trap for a non-technical curator."""
    iri = service.get_diseases_list()[0]["iri"]
    numeric = next(k for k, spec in service.EDITABLE.items()
                   if spec[0] == "data" and spec[2] in (int, float))

    detail = service.update_disease(iri, {numeric: "about 30 per 100k"}, editor="ada")

    assert len(detail["rejected"]) == 1
    bad = detail["rejected"][0]
    assert bad["field"] == numeric
    assert bad["value"] == "about 30 per 100k"
    assert "expected a number" in bad["reason"]


def test_an_unknown_field_is_reported_rather_than_ignored(service):
    iri = service.get_diseases_list()[0]["iri"]
    detail = service.update_disease(iri, {"not_a_field": "x"}, editor="ada")
    assert [r["field"] for r in detail["rejected"]] == ["not_a_field"]
    assert "not an editable field" in detail["rejected"][0]["reason"]


def test_a_good_save_reports_nothing_rejected(service):
    iri = service.get_diseases_list()[0]["iri"]
    detail = service.update_disease(iri, {"disease_category": "ZZZ-Test"}, editor="ada")
    assert detail["rejected"] == []
    assert detail["disease_category"] == ["ZZZ-Test"]


# ------------------------------------------------------- the changelog no-op
def test_the_changelog_is_written_even_when_the_property_is_undeclared(service):
    """It used to look the property up and return silently when absent, so on
    any ontology without ARI_ChangeLog every edit was recorded nowhere while
    the caller still reported success."""
    prop = service.world[service.base + "ARI_ChangeLog"]
    if prop is not None:
        from owlready2 import destroy_entity
        destroy_entity(prop)

    iri = service.get_diseases_list()[0]["iri"]
    before = len(service.get_disease_detail(iri)["changelog"])
    detail = service.update_disease(iri, {"disease_category": "ZZZ-Test"}, editor="ada")

    assert len(detail["changelog"]) == before + 1
    assert "ada" in detail["changelog"][-1]


# ------------------------------------------------------- exception handlers
def test_a_missing_disease_is_a_404():
    r = client.get("/api/v2/disease/http%3A%2F%2Fexample.org%2Fnope")
    assert r.status_code == 404
    assert "No entity" in r.json()["detail"]


def test_not_found_is_a_key_error_so_service_code_still_catches_it():
    """`except KeyError` blocks in ontology_service must keep working."""
    assert issubclass(NotFound, KeyError)
    assert issubclass(Invalid, ValueError)


def test_not_found_does_not_quote_its_message():
    """KeyError.__str__ wraps its argument in quotes, which reached the client."""
    assert str(NotFound("No entity with IRI: x")) == "No entity with IRI: x"


def test_an_incidental_key_error_is_not_dressed_up_as_a_404(monkeypatch):
    """A dictionary bug used to become a 404 carrying an internal key name, and
    never produced a traceback. It must surface as a server error instead."""
    def _boom(request):
        raise KeyError("some_internal_key")

    monkeypatch.setattr(main, "service_for", _boom)
    with pytest.raises(KeyError):
        client.get("/api/v2/diseases")


# --------------------------------------------------------- prediction cache
def test_predictions_are_cached_until_the_ontology_changes(service, monkeypatch):
    calls = []
    from app import predict_service as ps
    real = ps.predict_matches
    monkeypatch.setattr(ps, "predict_matches",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])

    service.predict_xrefs()
    service.predict_xrefs()
    assert len(calls) == 1, "the matching pass ran twice for an unchanged ontology"

    iri = service.get_diseases_list()[0]["iri"]
    service.update_disease(iri, {"disease_category": "ZZZ-Test"}, editor="ada")
    service.predict_xrefs()
    assert len(calls) == 2, "an edited ontology must re-run the matching pass"


# --------------------------------------------------------------- version
def test_the_version_carries_the_sha_the_readme_documents():
    import re
    # 2.<count> (<sha>, <date>) — or the 2.x fallback outside a git checkout.
    assert main.APP_VERSION == "2.x" or re.fullmatch(
        r"2\.\d+ \([0-9a-f]{7,}, \d{4}-\d{2}-\d{2}\)", main.APP_VERSION)
