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


def test_build_never_writes_literal_none_for_missing_name_or_ari_id():
    # A row whose disease has no ari_id/name yet (explicit None, not a missing
    # key) must render as an empty column, not the literal string "None".
    out = ss.build([_confirmed(ari_id=None, name=None)], author="a")
    data_line = next(ln for ln in out["sssom"].splitlines() if "SNOMEDCT:12345" in ln)
    assert "None" not in data_line
    assert data_line.split("\t")[0] == ""       # subject_id
    assert data_line.split("\t")[1] == ""       # subject_label
    equiv_line = next(ln for ln in out["equiv"].splitlines() if "SNOMEDCT" in ln)
    assert "None" not in equiv_line


def test_build_skips_null_placeholder_ids():
    # A stray null/placeholder id (e.g. from a UI bug that records a verdict
    # before an id is entered) must not turn into a "PREFIX:null" mapping row.
    out = ss.build([_confirmed(ids=["12345", "null", None, "  ", "None"])], author="a")
    assert out["added"] == 1
    lines = [ln for ln in out["sssom"].splitlines() if ln.startswith("ARI:0001001\t")]
    assert len(lines) == 1
    assert lines[0].split("\t")[4] == "SNOMEDCT:12345"


def test_build_does_not_double_the_prefix_on_an_already_prefixed_id():
    # An id stored or pasted as "MONDO:0014523" must not have MONDO prepended a
    # second time. That is what published ARI:0003 -> MONDO:MONDO:0014523.
    out = ss.build([_confirmed(db="mondo", ids=["MONDO:0014523"])], author="a")
    assert out["added"] == 1
    row = next(ln for ln in out["sssom"].splitlines() if ln.startswith("ARI:0001001\t")).split("\t")
    assert row[4] == "MONDO:0014523"
    equiv = next(ln for ln in out["equiv"].splitlines() if ln.startswith("ARI\t"))
    assert equiv.split("\t")[5] == "0014523"   # target_id is the bare local part


def test_build_dedups_prefixed_and_bare_spellings_of_one_id():
    # The two spellings are the same mapping, so once both normalise to the same
    # object CURIE the second must not be appended as a separate row.
    out = ss.build([_confirmed(db="mondo", ids=["MONDO:0014523", "0014523"])], author="a")
    assert out["added"] == 1


def test_build_absent_writes_no_term_found_with_object_source():
    out = ss.build([], author="a", absent=[{"ari_id": "ARI:0001001", "name": "Test disease",
                                            "db": "snomed"}])
    assert out["added"] == 1
    row = next(ln for ln in out["sssom"].splitlines() if ln.startswith("ARI:0001001\t")).split("\t")
    assert row[4] == ss.NO_TERM          # object_id
    assert row[5] == "SNOMEDCT"          # object_source names the empty database
    assert row[3] == ""                  # NoTermFound is not itself a negative
    assert "manual-absent" in out["equiv"]


def test_absences_for_two_databases_stay_distinct():
    # Both rows share the NoTermFound object, so object_source has to be part of
    # the dedup key or the second database's judgment would vanish.
    out = ss.build([], author="a", absent=[
        {"ari_id": "ARI:0001001", "name": "D", "db": "snomed"},
        {"ari_id": "ARI:0001001", "name": "D", "db": "mondo"}])
    assert out["added"] == 2
    judged = {j["prefix"]: j["judgment"] for j in ss.load_judgments(out["sssom"])}
    assert judged == {"SNOMEDCT": "absent", "MONDO": "absent"}


def test_load_judgments_reads_absent_from_equiv_fallback():
    out = ss.build([], author="a", absent=[{"ari_id": "ARI:0001001", "name": "D", "db": "mondo"}])
    judged = ss.load_judgments("", out["equiv"])
    assert [(j["prefix"], j["id"], j["judgment"]) for j in judged] == \
           [("MONDO", ss.NO_TERM_ID, "absent")]


def test_merge_remaps_rows_written_under_an_older_header():
    # A file written before object_source existed has one fewer field; re-merging
    # it must realign by column name rather than shifting every value right.
    legacy = ("subject_id\tsubject_label\tpredicate_id\tpredicate_modifier\tobject_id\t"
              "mapping_justification\tauthor_id\tmapping_date\n"
              "ARI:0001001\tTest disease\tskos:exactMatch\t\tSNOMEDCT:12345\t"
              "semapv:ManualMappingCuration\ta\t2026-01-01\n")
    out = ss.build([_confirmed(ids=["12345"])], author="a", existing_sssom=legacy)
    rows = [ln for ln in out["sssom"].splitlines() if ln.startswith("ARI:0001001\t")]
    assert len(rows) == 1                # the legacy row is recognised, not duplicated
    assert rows[0].split("\t")[5] == "SNOMEDCT"   # object_source backfilled
    judged = ss.load_judgments(out["sssom"])
    assert [(j["id"], j["judgment"]) for j in judged] == [("12345", "positive")]
