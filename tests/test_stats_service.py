"""The curation figures, derived from stores that already exist (issue #124)."""
from app import stats_service as st

ROWS = [
    {"iri": "d1", "ari_id": "ARI:1", "name": "One", "mondo": ["0001"], "snomed": ["11"], "doid": []},
    {"iri": "d2", "ari_id": "ARI:2", "name": "Two", "mondo": ["0002"], "snomed": [], "doid": []},
    {"iri": "d3", "ari_id": "ARI:3", "name": "Three", "mondo": [], "snomed": ["33"], "doid": []},
]

JUDGMENTS = [
    {"ari_id": "ARI:1", "prefix": "MONDO", "id": "0001", "dbs": ["mondo"], "judgment": "positive",
     "author": "github:ana", "date": "2026-08-03"},
    {"ari_id": "ARI:2", "prefix": "MONDO", "id": "0002", "dbs": ["mondo"], "judgment": "negative",
     "author": "github:ben", "date": "2026-08-10"},
    {"ari_id": "ARI:1", "prefix": "SNOMEDCT", "id": "11", "dbs": ["snomed"], "judgment": "positive",
     "author": "github:ana", "date": "2026-08-11"},
]


def _db(coverage, key):
    return next(c for c in coverage if c["key"] == key)


def test_coverage_separates_judged_from_merely_on_file():
    cov = st.coverage(ROWS, JUDGMENTS)
    mondo = _db(cov, "mondo")
    assert mondo["with_id"] == 2 and mondo["blank"] == 1
    assert mondo["confirmed"] == 1 and mondo["rejected"] == 1 and mondo["unjudged"] == 0
    # ARI:3's SNOMED id 33 is on file and nobody has judged it — the stall signal.
    snomed = _db(cov, "snomed")
    assert snomed["with_id"] == 2 and snomed["confirmed"] == 1 and snomed["unjudged"] == 1


def test_a_rejection_is_counted_even_though_the_id_was_removed():
    """Flagging a mapping usually removes the id, so counting only surviving ids
    reports zero rejections everywhere — which is the opposite of the signal."""
    rows = [{"iri": "d", "ari_id": "ARI:7", "name": "Seven", "mondo": []}]
    judgments = [{"ari_id": "ARI:7", "prefix": "MONDO", "id": "9", "dbs": ["mondo"],
                  "judgment": "negative", "author": "github:ana", "date": "2026-08-01"}]
    mondo = _db(st.coverage(rows, judgments), "mondo")
    assert mondo["rejected"] == 1 and mondo["with_id"] == 0 and mondo["blank"] == 1


def test_a_cell_is_only_confirmed_when_every_id_in_it_is():
    rows = [{"iri": "d", "ari_id": "ARI:9", "name": "Nine", "mondo": ["1", "2"]}]
    judgments = [{"ari_id": "ARI:9", "prefix": "MONDO", "id": "1", "judgment": "positive"}]
    mondo = _db(st.coverage(rows, judgments), "mondo")
    assert mondo["confirmed"] == 0 and mondo["unjudged"] == 1


def test_curators_are_counted_separately_for_judging_and_for_adding():
    """The two-person rule means they are never the same person for one id."""
    authors = {"d1|snomed|11": "cara", "d2|mondo|0002": "cara"}
    rows = st.by_curator(JUDGMENTS, authors)
    ana = next(r for r in rows if r["curator"] == "github:ana")
    assert ana["confirmed"] == 2 and ana["flagged"] == 0
    assert ana["first"] == "2026-08-03" and ana["last"] == "2026-08-11"
    cara = next(r for r in rows if r["curator"] == "github:cara")
    assert cara["ids_added"] == 2 and cara["confirmed"] == 0
    # Busiest first, so the table opens on the people doing the work.
    assert rows[0]["curator"] == "github:ana"


def test_an_unattributed_judgment_is_named_rather_than_dropped():
    rows = st.by_curator([{"ari_id": "A", "prefix": "P", "id": "1",
                           "judgment": "positive", "author": "", "date": "2026-01-01"}], {})
    assert rows[0]["curator"] == "unattributed" and rows[0]["confirmed"] == 1


def test_weeks_group_by_iso_week_and_keep_the_most_recent():
    weeks = st.by_week(JUDGMENTS)
    assert [w["week"] for w in weeks] == ["2026-W32", "2026-W33"]
    assert weeks[1]["confirmed"] == 1 and weeks[1]["flagged"] == 1


def test_an_unparseable_date_does_not_invent_a_week():
    assert st.by_week([{"judgment": "positive", "date": "soon"}]) == []
    assert st.by_week([{"judgment": "positive", "date": None}]) == []


def test_waiting_counts_only_ids_the_ledger_can_attribute():
    """An id with no recorded author is nobody's to route.

    The ledger cannot say whose it is, so the two-person rule does not apply and
    counting it would drown the number in every unjudged cell in the matrix.
    """
    authors = {"d3|snomed|33": "cara"}
    w = st.waiting(ROWS, JUDGMENTS, authors)
    assert w["ids_unjudged"] == 1               # ARI:3's SNOMED 33
    assert w["needs_second_reviewer"] == 1
    assert w["by_adder"] == [{"curator": "cara", "ids": 1}]

    assert st.waiting(ROWS, JUDGMENTS, {})["needs_second_reviewer"] == 0


def test_queues_read_the_assignment_stores_own_shape():
    q = st.queues({"ana": {"iris": ["a", "b", "c"], "done": ["a"], "updated": "2026-08-01"}})
    assert q["assigned"] == 3 and q["done"] == 1
    assert q["curators"][0] == {"curator": "ana", "assigned": 3, "done": 1,
                                "updated": "2026-08-01"}


def test_build_answers_every_question_the_issue_lists():
    d = st.build(ROWS, JUDGMENTS, {"d3|snomed|33": "cara"}, {})
    assert d["diseases"] == 3 and d["judgments"] == 3
    for section in ("coverage", "curators", "weeks", "waiting", "queues", "generated"):
        assert d[section] or d[section] == 0, section
