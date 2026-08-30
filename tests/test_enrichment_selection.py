"""Per-item enrichment selection and per-value lineage (issue #117).

The preview offered one all-or-nothing checkbox, so a curator who wanted eight
of eleven proposed synonyms had to decline all eleven — and once applied, the
PR body recorded only counts, so six months later nobody could tell whether a
synonym came from MONDO, from DOID, or from a human.
"""
import pytest

from app.ontology_service import _pick_entries

ENTRIES = [
    {"value": "juvenile diabetes", "source": "MONDO:0005147"},
    {"value": "sugar diabetes", "source": "DOID:9744"},
    {"value": "IDDM", "source": "MONDO:0005147"},
]


def test_no_selection_means_everything():
    """The old behaviour, preserved for any caller that does not select."""
    assert _pick_entries(ENTRIES, None) == ENTRIES


def test_an_empty_selection_means_nothing():
    """Distinct from None: the curator unticked every box."""
    assert _pick_entries(ENTRIES, []) == []


def test_only_the_ticked_values_are_kept():
    kept = _pick_entries(ENTRIES, ["juvenile diabetes", "IDDM"])
    assert [e["value"] for e in kept] == ["juvenile diabetes", "IDDM"]


def test_selection_matches_on_value_not_position():
    """A stale selection must not apply the wrong proposal.

    The preview and the publish are two requests; the indexes could have been
    rebuilt in between. Matching on the value means anything no longer proposed
    is dropped rather than silently swapped for whatever now sits at that
    index."""
    kept = _pick_entries(ENTRIES, ["sugar diabetes", "a value no longer proposed"])
    assert [e["value"] for e in kept] == ["sugar diabetes"]


def test_lineage_survives_selection():
    kept = _pick_entries(ENTRIES, ["sugar diabetes"])
    assert kept[0]["source"] == "DOID:9744"


def test_nothing_proposed_is_handled():
    assert _pick_entries(None, ["anything"]) == []
    assert _pick_entries([], None) == []


# --------------------------------------------------------- against the real ontology
@pytest.fixture
def enriched(service):
    """A disease with one confirmed MONDO mapping, enriched."""
    iri = next(r["iri"] for r in service.get_xref_rows() if r.get("mondo"))
    row = next(r for r in service.get_xref_rows() if r["iri"] == iri)
    confirmed = [{"iri": iri, "db": "mondo", "ids": row["mondo"]}]
    return service, iri, confirmed


def test_preview_entries_carry_a_source(enriched):
    service, iri, confirmed = enriched
    preview = service.enrichment_preview(confirmed)
    for add in preview.values():
        for entry in add["synonyms"] + add["subtypes"]:
            assert entry["value"] and entry["source"], entry


def test_applying_records_where_each_value_came_from(enriched):
    service, iri, confirmed = enriched
    preview = service.enrichment_preview(confirmed)
    if not preview:
        pytest.skip("no enrichment proposed for this ontology")
    target = next(iter(preview))

    service.apply_enrichment(confirmed, editor="github:ada")

    lineage = service._get_annotation(service._entity(target),
                                      service.base + "ARI_EnrichmentSource")
    assert lineage, "every applied value must name the term it came from"
    for line in lineage:
        assert " | from " in line and "github:ada" in line


def test_declining_everything_applies_nothing(enriched):
    service, iri, confirmed = enriched
    preview = service.enrichment_preview(confirmed)
    if not preview:
        pytest.skip("no enrichment proposed for this ontology")
    empty = {i: {"synonyms": [], "subtypes": []} for i in preview}

    got = service.apply_enrichment(confirmed, editor="ada", selected=empty)

    assert got == {"diseases": 0, "synonyms_added": 0, "subtypes_added": 0}


def test_keeping_one_synonym_applies_only_that_one(enriched):
    service, iri, confirmed = enriched
    preview = service.enrichment_preview(confirmed)
    target = next((i for i, a in preview.items() if a["synonyms"]), None)
    if target is None:
        pytest.skip("no synonyms proposed for this ontology")
    keep = preview[target]["synonyms"][0]["value"]
    selected = {i: {"synonyms": [], "subtypes": []} for i in preview}
    selected[target] = {"synonyms": [keep], "subtypes": []}

    before = service._get_annotation(service._entity(target), service.base + "ARI_Synonym")
    got = service.apply_enrichment(confirmed, editor="ada", selected=selected)
    after = service._get_annotation(service._entity(target), service.base + "ARI_Synonym")

    assert got["synonyms_added"] == 1
    assert got["subtypes_added"] == 0
    assert [s for s in after if s not in before] == [keep]
