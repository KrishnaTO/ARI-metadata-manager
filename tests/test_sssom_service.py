"""Coverage for the SSSOM / equivalency mapping builder + loader.

Pure functions, no ontology needed. These encode the biomappings-style contract
(positive vs. negative judgments, dedup keys) that the reference-review page and
the publish endpoint both depend on.
"""
from app import sssom_service as ss


def _confirmed(**kw):
    base = {"ari_id": "ARI:0001001", "name": "Test disease", "db": "snomed", "ids": ["12345"]}
    base.update(kw)
    return base


def test_prefix_to_dbs_groups_shared_prefixes():
    # SNOMEDCT backs both the snomed and dxcode review columns.
    assert set(ss.PREFIX_TO_DBS["SNOMEDCT"]) == {"snomed", "dxcode"}


def test_build_positive_mapping():
    out = ss.build([_confirmed()], author="orcid:0000-0000-0000-0000")
    assert out["added"] == 1
    sssom = out["sssom"]
    assert "SNOMEDCT:12345" in sssom
    assert "skos:exactMatch" in sssom
    # positive rows carry no predicate modifier
    data_lines = [ln for ln in sssom.splitlines() if ln.startswith("ARI:0001001\t")]
    assert data_lines and data_lines[0].split("\t")[3] == ""  # predicate_modifier empty
    assert "manual" in out["equiv"]


def test_build_negative_mapping_uses_not_modifier():
    out = ss.build([], author="curator", flagged=[_confirmed(ids=["999"])])
    assert out["added"] == 1
    row = next(ln for ln in out["sssom"].splitlines() if ln.startswith("ARI:0001001\t"))
    assert row.split("\t")[3] == "Not"
    assert "manual-negative" in out["equiv"]


def test_positive_and_negative_for_same_triple_coexist():
    # Same subject+object but opposite verdicts must not collapse into one row.
    out = ss.build([_confirmed(ids=["555"])], author="a", flagged=[_confirmed(ids=["555"])])
    rows = [ln for ln in out["sssom"].splitlines() if "SNOMEDCT:555" in ln]
    modifiers = sorted(r.split("\t")[3] for r in rows)
    assert modifiers == ["", "Not"]


def test_build_is_idempotent_on_reaccumulate():
    first = ss.build([_confirmed()], author="a")
    again = ss.build([_confirmed()], author="a",
                     existing_sssom=first["sssom"], existing_equiv=first["equiv"])
    assert again["added"] == 0


def test_load_judgments_round_trips_positive_and_negative():
    built = ss.build([_confirmed(ids=["12345"])], author="a",
                     flagged=[_confirmed(ids=["999"])])
    judged = ss.load_judgments(built["sssom"])
    by_id = {j["id"]: j for j in judged}
    assert by_id["12345"]["judgment"] == "positive"
    assert by_id["999"]["judgment"] == "negative"
    assert by_id["12345"]["ari_id"] == "ARI:0001001"
    assert by_id["12345"]["prefix"] == "SNOMEDCT"
    assert "snomed" in by_id["12345"]["dbs"]


def test_load_judgments_prefers_sssom_over_equiv():
    built = ss.build([_confirmed()], author="a")
    # When both are given, SSSOM is canonical; equiv is a fallback only.
    judged = ss.load_judgments(built["sssom"], "garbage\theader\nonly")
    assert judged and all(j["prefix"] == "SNOMEDCT" for j in judged)


def test_load_judgments_empty_input():
    assert ss.load_judgments("", "") == []
