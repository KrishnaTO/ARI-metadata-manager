"""Service-layer tests for per-curator assignments and mapping decisions."""
import pytest

from app import assignment_service as asv


@pytest.fixture
def store(tmp_path):
    return asv.AssignmentStore(tmp_path / "assignments")


DBS = [
    {"key": "snomed", "label": "SNOMED", "prefix": "SNOMEDCT", "review": True,
     "noframe": False, "link": "s/{num}", "search": "s?q={name}"},
    {"key": "doid", "label": "DOID", "prefix": "DOID", "review": True,
     "noframe": False, "link": "d/{num}", "search": "d?q={name}"},
    {"key": "mondo", "label": "MONDO", "prefix": "MONDO", "review": True,
     "noframe": False, "link": "m/{num}", "search": "m?q={name}"},
    {"key": "dxcode", "label": "DXCODE", "prefix": "SNOMEDCT", "review": False,
     "noframe": False, "link": None, "search": None},
]

ROW = {
    "iri": "http://ari/AIH", "ari_id": "ARI:0031", "name": "Autoimmune hepatitis",
    "synonyms": ["AIH", "Lupoid hepatitis"],
    "snomed": ["408335007"], "doid": ["DOID:2043"], "mondo": [],
}


# ---------------------------------------------------------------- assignments
def test_assign_is_additive_and_deduplicates(store):
    store.assign("ktokey", ["a", "b"])
    rec = store.assign("ktokey", ["b", "c"])
    assert rec["iris"] == ["a", "b", "c"]


def test_replace_drops_previous_and_prunes_done(store):
    store.assign("ktokey", ["a", "b"])
    store.set_done("ktokey", "a")
    rec = store.assign("ktokey", ["c"], replace=True)
    assert rec["iris"] == ["c"]
    assert rec["done"] == []


def test_assign_requires_a_login(store):
    with pytest.raises(ValueError):
        store.assign("", ["a"])


def test_unassign_removes_and_unknown_login_raises(store):
    store.assign("ktokey", ["a", "b"])
    assert store.unassign("ktokey", ["a"])["iris"] == ["b"]
    with pytest.raises(KeyError):
        store.unassign("nobody", ["a"])


def test_owner_of(store):
    store.assign("ktokey", ["a"])
    assert store.owner_of("a") == "ktokey"
    assert store.owner_of("z") is None


def test_done_toggles(store):
    store.assign("ktokey", ["a"])
    assert store.set_done("ktokey", "a")["done"] == ["a"]
    assert store.set_done("ktokey", "a", done=False)["done"] == []


def test_state_survives_a_new_store_instance(store, tmp_path):
    store.assign("ktokey", ["a"])
    store.decide("ktokey", "http://ari/AIH", "doid", "DOID:2043", "confirm")
    fresh = asv.AssignmentStore(tmp_path / "assignments")
    assert fresh.assigned_to("ktokey")["iris"] == ["a"]
    assert len(fresh.decisions("ktokey")) == 1


# ------------------------------------------------------------------ decisions
def test_decide_is_idempotent_per_id(store):
    store.decide("ktokey", ROW["iri"], "doid", "DOID:2043", "confirm")
    store.decide("ktokey", ROW["iri"], "doid", "DOID:2043", "reject")
    items = store.decisions("ktokey")
    assert len(items) == 1
    assert items[0]["verdict"] == "reject"


def test_decide_rejects_unknown_verdict(store):
    with pytest.raises(ValueError):
        store.decide("ktokey", ROW["iri"], "doid", "DOID:2043", "maybe")


def test_decide_requires_an_id_unless_no_value(store):
    with pytest.raises(ValueError):
        store.decide("ktokey", ROW["iri"], "doid", "", "confirm")
    entry = store.decide("ktokey", ROW["iri"], "orphanet", "", "no_value")
    assert entry["object_id"] == ""


def test_undo_removes_one_decision(store):
    e = store.decide("ktokey", ROW["iri"], "doid", "DOID:2043", "confirm")
    store.undo("ktokey", e["id"])
    assert store.decisions("ktokey") == []
    with pytest.raises(KeyError):
        store.undo("ktokey", e["id"])


def test_decisions_are_per_curator(store):
    store.decide("ktokey", ROW["iri"], "doid", "DOID:2043", "confirm")
    assert store.decisions("rchen") == []


# --------------------------------------------------------------- publish view
def test_publish_payload_groups_by_cell_and_splits_verdicts(store):
    iri = ROW["iri"]
    store.decide("ktokey", iri, "doid", "DOID:2043", "confirm",
                 name=ROW["name"], ari_id=ROW["ari_id"])
    store.decide("ktokey", iri, "snomed", "408335007", "confirm",
                 name=ROW["name"], ari_id=ROW["ari_id"])
    store.decide("ktokey", iri, "umls", "C0019187", "reject",
                 name=ROW["name"], ari_id=ROW["ari_id"])
    store.decide("ktokey", iri, "orphanet", "", "no_value",
                 name=ROW["name"], ari_id=ROW["ari_id"])
    store.decide("ktokey", iri, "mesh", "D019693", "skip")

    p = store.to_publish_payload("ktokey")
    assert p["skipped"] == 1
    snomed = next(c for c in p["confirmed"] if c["db"] == "snomed")
    assert snomed["ids"] == ["408335007"]
    assert snomed["ari_id"] == "ARI:0031"
    assert {c["db"] for c in p["confirmed"]} == {"doid", "snomed"}
    assert {c["db"] for c in p["flagged"]} == {"umls", "orphanet"}
    # A "no correct value" decision publishes as a negative with no candidate id.
    assert next(c for c in p["flagged"] if c["db"] == "orphanet")["ids"] == []


def test_confirming_supersedes_a_sibling_confirm_in_the_same_database(store):
    """At most one id per cell may be the match, so the older confirm is rejected.

    Several ids are usually offered for one database and only one of them is the
    disease; publishing two ``skos:exactMatch`` rows for the same cell would assert
    a mapping the curator never made.
    """
    iri = ROW["iri"]
    first = store.decide("ktokey", iri, "snomed", "408335007", "confirm")
    assert first["superseded"] == []
    second = store.decide("ktokey", iri, "snomed", "999", "confirm")
    assert second["superseded"] == ["408335007"]

    by_id = {d["object_id"]: d["verdict"] for d in store.decisions("ktokey", iri)}
    assert by_id == {"408335007": "reject", "999": "confirm"}

    p = store.to_publish_payload("ktokey")
    assert next(c for c in p["confirmed"] if c["db"] == "snomed")["ids"] == ["999"]
    assert next(c for c in p["flagged"] if c["db"] == "snomed")["ids"] == ["408335007"]


def test_confirming_leaves_other_databases_alone(store):
    iri = ROW["iri"]
    store.decide("ktokey", iri, "doid", "DOID:2043", "confirm")
    entry = store.decide("ktokey", iri, "snomed", "408335007", "confirm")
    assert entry["superseded"] == []
    assert {c["db"] for c in store.to_publish_payload("ktokey")["confirmed"]} == {"doid", "snomed"}


def test_summary_counts(store):
    store.decide("ktokey", ROW["iri"], "doid", "DOID:2043", "confirm")
    store.decide("ktokey", ROW["iri"], "mondo", "MONDO:0016264", "confirm",
                 predicted=True)
    store.decide("ktokey", ROW["iri"], "mesh", "D019693", "skip")
    s = store.summary("ktokey")
    assert s["total"] == 3
    assert s["by_verdict"]["confirm"] == 2
    assert s["by_verdict"]["skip"] == 1
    assert s["diseases_touched"] == 1
    assert s["predicted_accepted"] == 1


def test_clear_empties_the_session(store):
    store.decide("ktokey", ROW["iri"], "doid", "DOID:2043", "confirm")
    assert store.clear("ktokey")["cleared"] == 1
    assert store.decisions("ktokey") == []


# ------------------------------------------------------------- disease panel
JUDGMENTS = [{"ari_id": "ARI:0031", "prefix": "SNOMEDCT", "id": "408335007",
              "judgment": "positive"}]
PREDICTIONS = [{"ari_id": "ARI:0031", "prefix": "MONDO", "id": "MONDO:0016264",
                "object_label": "autoimmune hepatitis", "match_field": "label",
                "confidence": "high"}]


def test_panel_skips_non_review_databases():
    panel = asv.disease_panel(ROW, DBS, [], [], [])
    assert [d["key"] for d in panel["databases"]] == ["snomed", "doid", "mondo"]
    assert panel["total"] == 3


def test_panel_statuses():
    panel = asv.disease_panel(ROW, DBS, JUDGMENTS, PREDICTIONS, [])
    status = {d["key"]: d["status"] for d in panel["databases"]}
    assert status == {"snomed": "confirmed", "doid": "unreviewed", "mondo": "predicted"}
    # Only the two that still need a human count as remaining.
    assert panel["remaining"] == 2


def test_panel_marks_decided_and_drops_it_from_remaining(store):
    store.decide("ktokey", ROW["iri"], "doid", "DOID:2043", "confirm")
    panel = asv.disease_panel(ROW, DBS, JUDGMENTS, PREDICTIONS,
                              store.decisions("ktokey"))
    doid = next(d for d in panel["databases"] if d["key"] == "doid")
    assert doid["status"] == "decided"
    assert doid["candidates"][0]["decision"]["verdict"] == "confirm"
    assert panel["remaining"] == 1


MULTI_ROW = {**ROW, "snomed": ["408335007", "999", "123"]}


def test_panel_holds_a_multi_id_database_open_until_every_id_is_judged(store):
    """One verdict must not settle a database that offers several ids.

    At most one of them is the disease, so settling on the first decision hid the
    rest — a curator who confirmed the wrong id never saw the right one.
    """
    iri = ROW["iri"]
    store.decide("ktokey", iri, "snomed", "999", "confirm")
    panel = asv.disease_panel(MULTI_ROW, DBS, [], [], store.decisions("ktokey"))
    snomed = next(d for d in panel["databases"] if d["key"] == "snomed")
    assert snomed["status"] == "partial"
    assert panel["remaining"] == 3          # snomed still open, plus doid and mondo

    store.decide("ktokey", iri, "snomed", "408335007", "reject")
    store.decide("ktokey", iri, "snomed", "123", "reject")
    panel = asv.disease_panel(MULTI_ROW, DBS, [], [], store.decisions("ktokey"))
    snomed = next(d for d in panel["databases"] if d["key"] == "snomed")
    assert snomed["status"] == "decided"
    assert panel["remaining"] == 2


def test_panel_settles_when_every_candidate_is_rejected(store):
    """Rejecting them all is a complete answer: "none of these" needs no extra click."""
    for oid in ("408335007", "999", "123"):
        store.decide("ktokey", ROW["iri"], "snomed", oid, "reject")
    panel = asv.disease_panel(MULTI_ROW, DBS, [], [], store.decisions("ktokey"))
    assert next(d for d in panel["databases"] if d["key"] == "snomed")["status"] == "decided"


def test_panel_counts_a_skip_as_judged(store):
    """Skip defers one id without holding its whole database open."""
    iri = ROW["iri"]
    store.decide("ktokey", iri, "snomed", "408335007", "skip")
    store.decide("ktokey", iri, "snomed", "999", "confirm")
    store.decide("ktokey", iri, "snomed", "123", "reject")
    panel = asv.disease_panel(MULTI_ROW, DBS, [], [], store.decisions("ktokey"))
    assert next(d for d in panel["databases"] if d["key"] == "snomed")["status"] == "decided"


def test_queue_coverage_reports_a_part_judged_database(store):
    store.assign("ktokey", [ROW["iri"]])
    store.decide("ktokey", ROW["iri"], "snomed", "999", "confirm")
    q = asv.queue_for("ktokey", store, [MULTI_ROW], DBS, [], [])
    assert q["diseases"][0]["coverage"] == ["partial", "unreviewed", "missing"]
    assert q["diseases"][0]["remaining"] == 3


def test_panel_hides_predictions_already_flagged_negative():
    negative = [{"ari_id": "ARI:0031", "prefix": "MONDO",
                 "id": "MONDO:0016264", "judgment": "negative"}]
    panel = asv.disease_panel(ROW, DBS, negative, PREDICTIONS, [])
    mondo = next(d for d in panel["databases"] if d["key"] == "mondo")
    assert mondo["candidates"] == []
    assert mondo["status"] == "missing"


def test_panel_no_value_decision_settles_a_missing_cell(store):
    store.decide("ktokey", ROW["iri"], "mondo", "", "no_value")
    panel = asv.disease_panel(ROW, DBS, [], [], store.decisions("ktokey"))
    mondo = next(d for d in panel["databases"] if d["key"] == "mondo")
    assert mondo["status"] == "decided"
    assert mondo["no_value_decision"]["verdict"] == "no_value"


def test_panel_carries_prediction_metadata():
    panel = asv.disease_panel(ROW, DBS, [], PREDICTIONS, [])
    cand = next(d for d in panel["databases"] if d["key"] == "mondo")["candidates"][0]
    assert cand["predicted"] is True
    assert cand["match_field"] == "label"
    assert cand["confidence"] == "high"
    assert cand["label"] == "autoimmune hepatitis"


# --------------------------------------------------------------------- queue
def test_queue_reports_progress_and_coverage(store):
    store.assign("ktokey", [ROW["iri"]])
    q = asv.queue_for("ktokey", store, [ROW], DBS, JUDGMENTS, PREDICTIONS)
    assert q["counts"] == {"diseases": 1, "diseases_done": 0, "references": 3,
                           "remaining": 2, "reviewed": 1}
    assert q["diseases"][0]["coverage"] == ["confirmed", "unreviewed", "predicted"]
    assert q["unpublished"] == 0


def test_queue_ignores_assignments_missing_from_the_ontology(store):
    store.assign("ktokey", [ROW["iri"], "http://ari/GONE"])
    q = asv.queue_for("ktokey", store, [ROW], DBS, [], [])
    assert len(q["diseases"]) == 1


def test_queue_sorts_unfinished_first_by_work_left(store):
    other = dict(ROW, iri="http://ari/MS", ari_id="ARI:0112", name="Multiple sclerosis",
                 snomed=["24700007"], doid=["DOID:2377"], mondo=["MONDO:0005301"])
    store.assign("ktokey", [ROW["iri"], other["iri"]])
    store.set_done("ktokey", ROW["iri"])
    q = asv.queue_for("ktokey", store, [ROW, other], DBS, [], [])
    assert [d["name"] for d in q["diseases"]] == ["Multiple sclerosis",
                                                   "Autoimmune hepatitis"]
    assert q["counts"]["diseases_done"] == 1
