# Working-Copy Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep each curator's working copy current with the source branch by three-way merging it, and let curators choose per field where both sides changed the same thing.

**Architecture:** Each working copy gets an ancestor file (the version it started from). `merge_service` merges at the triple level — the working copy (mine), the branch (theirs) and the ancestor (base) — one disease plus its item individuals at a time. A new `POST /api/v2/sync` runs that merge at page load, publish runs it before committing, and `POST /api/v2/resolve` applies the curator's per-field choices from a new `UIDialog.merge` dialog shared by both pages.

**Tech Stack:** Python 3.14, FastAPI, owlready2 (raw triple API), pytest; vanilla JS classic scripts, native `<dialog>`.

**Spec:** `docs/superpowers/specs/2026-09-22-working-copy-merge-design.md`

## Global Constraints

- Branch: `claude/ari-edits-submit-conflict-f23669` (already checked out in this worktree). Never commit to `main`.
- Stage only the files you edited (`git add <paths>`); never `git add -A` or `git checkout -- .` — `ontologies/ari_t1d.owl` shows spurious CRLF changes.
- Ancestor lives at `.user-data/ancestor/<login>.owl` + `.user-data/ancestor/<login>.json` (`{"sha": ...}`) — never directly in `.user-data/` (the sweep globs `*.owl` there).
- Conflict key format: `"<subject iri>|<predicate iri>"`; a whole-item (deleted vs edited) conflict uses `"<subject iri>|"`.
- Choice values are exactly `"mine"` or `"theirs"`.
- Conflict payload: `{"iri", "name", "upstream_log": [str], "fields": [{"key", "label", "subject", "mine", "theirs", "base"}]}`.
- owlready2 caches entity values on Python objects; after any raw-triple write to a service, save it and reload it from disk (evict from `workspace.USER_SVC`) before reading through the object API.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- The app version is git-derived (`config.APP_VERSION`); no manual bump.
- Test command: `python -m pytest -q` from the worktree root; lint: `ruff check app tests`.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/merge_service.py` (modify) | Triple snapshots, writing a subject, three-way merge of one disease, merging a set of diseases into a working copy + ancestor. Loses `upstream_edits`. |
| `app/workspace.py` (modify) | Ancestor storage (paths, read/write/drop), `merge_from` (snapshot → merge → save → evict). Loses `forget`. |
| `app/github_service.py` (modify) | `branch_sha` helper. |
| `app/routes/settings.py` (modify) | `_fetch_branch` records the ancestor at the fetched sha. |
| `app/routes/publish.py` (modify) | `_baseline_service` takes a `ref`; publish merges instead of `upstream_edits`; `/api/v2/discard` removed. |
| `app/routes/merge.py` (create) | `POST /api/v2/sync`, `POST /api/v2/resolve`. |
| `app/routes/__init__.py` (modify) | Register `merge.router`. |
| `static/js/ui-dialog.js`, `static/css/ui-dialog.css` (modify) | `UIDialog.merge(conflicts)`. |
| `static/js/github.js` (modify) | Editor: sync at sign-in, conflict banner, publish 409 → merge dialog → resolve → retry. |
| `static/ref-edits/ref-edits.js` (modify) | Review page: same flows. |
| `tests/test_merge_service.py` (create) | Merge logic. |
| `tests/test_working_copy_sync.py` (create) | Ancestor storage + sync/resolve/publish routes. |
| `tests/test_publish_rebase.py`, `tests/test_ref_session.py` (modify) | Drop tests of removed `upstream_edits` / `forget`. |
| `README.md`, `changelog.md` (modify) | Docs. |

---

### Task 1: Triple snapshots and the three-way merge of one disease

**Files:**
- Modify: `app/merge_service.py`
- Create: `tests/test_merge_service.py`

**Interfaces:**
- Produces:
  - `merge_service._triples(svc, iri) -> dict[str, set[tuple]] | None` — `{predicate iri: {("o", obj_iri) | ("d", value, datatype_iri_or_lang)}}`, `None` when `svc` says nothing about `iri`.
  - `merge_service._write(dst, iri, props, sources) -> None` — replace everything `dst` says about `iri` with `props` (`None` deletes it); declares unseen annotation properties from the first of `sources` that knows them.
  - `merge_service.Merge` dataclass: `subjects: dict[str, dict | None]`, `conflicts: list[dict]`, `upstream_log: list[str]`, `name: str`.
  - `merge_service.merge_disease(base, mine, theirs, iri, choices=None) -> Merge` — `base` may be `None` (no ancestor).
  - `merge_service.apply(dst, merge, sources) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_merge_service.py`:

```python
"""Three-way merge of a curator's working copy with the source branch.

``base`` is the version the working copy started from, ``mine`` the working copy,
``theirs`` the branch. Each fixture service is an independent copy of the same
ontology, so all three start identical — exactly an ancestor and two descendants.
"""
import pytest

from app import merge_service
from app.ontology_service import OntologyService


@pytest.fixture
def trio(make_service):
    """(base, mine, theirs)."""
    return make_service(), make_service(), make_service()


def _iri(svc, n=0):
    return svc.get_diseases_list()[n]["iri"]


def _with_synonyms(svc):
    return next(d["iri"] for d in svc.get_diseases_list()
                if svc.get_disease_detail(d["iri"])["synonyms"])


def _with_symptom(svc):
    for d in svc.get_diseases_list():
        syms = svc.get_disease_detail(d["iri"])["symptoms"]
        if syms:
            return d["iri"], syms[0]["iri"]
    raise AssertionError("fixture ontology has no disease with a symptom")


def _reloaded(svc):
    svc._save()
    return OntologyService(str(svc.path))


def test_a_field_only_they_changed_comes_in(trio):
    base, mine, theirs = trio
    d = _iri(mine)
    theirs.update_disease(d, {"definition": "theirs"}, editor="bob")

    m = merge_service.merge_disease(base, mine, theirs, d)
    assert m.conflicts == []
    merge_service.apply(mine, m, (mine, theirs))

    assert _reloaded(mine).get_disease_detail(d)["definition"] == "theirs"


def test_a_field_only_i_changed_is_kept(trio):
    # Also the submitted-but-unmerged case: my edit is not on the branch yet.
    base, mine, theirs = trio
    d = _iri(mine)
    mine.update_disease(d, {"definition": "mine"}, editor="ada")

    m = merge_service.merge_disease(base, mine, theirs, d)
    merge_service.apply(mine, m, (mine, theirs))

    assert _reloaded(mine).get_disease_detail(d)["definition"] == "mine"


def test_both_changing_one_field_is_a_conflict_that_names_both_values(trio):
    base, mine, theirs = trio
    d = _iri(mine)
    mine.update_disease(d, {"definition": "mine"}, editor="ada")
    theirs.update_disease(d, {"definition": "theirs"}, editor="bob")

    m = merge_service.merge_disease(base, mine, theirs, d)

    [c] = m.conflicts
    assert c["key"] == f"{d}|http://www.w3.org/2000/01/rdf-schema#comment"
    assert c["label"] == "Definition"
    assert (c["mine"], c["theirs"]) == ("mine", "theirs")
    assert any("| bob |" in e for e in m.upstream_log)


@pytest.mark.parametrize("pick,expected", [("mine", "mine"), ("theirs", "theirs")])
def test_a_choice_settles_the_conflict(trio, pick, expected):
    base, mine, theirs = trio
    d = _iri(mine)
    mine.update_disease(d, {"definition": "mine"}, editor="ada")
    theirs.update_disease(d, {"definition": "theirs"}, editor="bob")
    key = f"{d}|http://www.w3.org/2000/01/rdf-schema#comment"

    m = merge_service.merge_disease(base, mine, theirs, d, {key: pick})
    assert m.conflicts == []
    merge_service.apply(mine, m, (mine, theirs))

    assert _reloaded(mine).get_disease_detail(d)["definition"] == expected


def test_list_fields_merge_value_by_value(trio):
    base, mine, theirs = trio
    d = _with_synonyms(mine)
    existing = mine.get_disease_detail(d)["synonyms"]
    withdrawn, kept = existing[0], existing[1:]
    mine.update_disease(d, {"synonyms": ", ".join(existing + ["added by me"])}, editor="ada")
    theirs.update_disease(d, {"synonyms": ", ".join(kept)}, editor="bob")

    m = merge_service.merge_disease(base, mine, theirs, d)
    assert m.conflicts == []
    merge_service.apply(mine, m, (mine, theirs))

    got = set(_reloaded(mine).get_disease_detail(d)["synonyms"])
    assert got == set(kept) | {"added by me"}


def test_both_sides_changelog_lines_are_kept(trio):
    base, mine, theirs = trio
    d = _iri(mine)
    mine.update_disease(d, {"definition": "mine"}, editor="ada")
    theirs.update_disease(d, {"age_range": "adults"}, editor="bob")

    m = merge_service.merge_disease(base, mine, theirs, d)
    merge_service.apply(mine, m, (mine, theirs))

    log = _reloaded(mine).get_disease_detail(d)["changelog"]
    assert any("| ada |" in e for e in log) and any("| bob |" in e for e in log)


@pytest.mark.parametrize("pick", ["mine", "theirs"])
def test_an_item_they_deleted_and_i_edited_is_a_conflict(trio, pick):
    base, mine, theirs = trio
    d, item = _with_symptom(mine)
    mine.update_item(item, "symptoms", {"symptomDescription": "edited by me"},
                     disease_iri=d, editor="ada")
    theirs.delete_item(item, "symptoms", d, editor="bob")

    m = merge_service.merge_disease(base, mine, theirs, d)
    assert [c["key"] for c in m.conflicts] == [f"{item}|"]

    m = merge_service.merge_disease(base, mine, theirs, d, {f"{item}|": pick})
    merge_service.apply(mine, m, (mine, theirs))

    kept = [s["iri"] for s in _reloaded(mine).get_disease_detail(d)["symptoms"]]
    assert (item in kept) is (pick == "mine")


def test_an_item_they_deleted_and_i_left_alone_is_deleted(trio):
    base, mine, theirs = trio
    d, item = _with_symptom(mine)
    theirs.delete_item(item, "symptoms", d, editor="bob")

    m = merge_service.merge_disease(base, mine, theirs, d)
    assert m.conflicts == []
    merge_service.apply(mine, m, (mine, theirs))

    assert item not in [s["iri"] for s in _reloaded(mine).get_disease_detail(d)["symptoms"]]


def test_without_an_ancestor_every_differing_single_field_is_asked(trio):
    _, mine, theirs = trio
    d = _iri(mine)
    theirs.update_disease(d, {"definition": "theirs"}, editor="bob")

    m = merge_service.merge_disease(None, mine, theirs, d)

    assert [c["label"] for c in m.conflicts] == ["Definition"]
    assert m.conflicts[0]["base"] is None


def test_without_an_ancestor_list_fields_keep_both_sides(trio):
    _, mine, theirs = trio
    d = _iri(mine)
    mine.update_disease(d, {"synonyms": "only mine"}, editor="ada")
    theirs.update_disease(d, {"synonyms": "only theirs"}, editor="bob")

    m = merge_service.merge_disease(None, mine, theirs, d)
    assert m.conflicts == []
    merge_service.apply(mine, m, (mine, theirs))

    assert set(_reloaded(mine).get_disease_detail(d)["synonyms"]) >= {"only mine", "only theirs"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_merge_service.py -q`
Expected: FAIL — `AttributeError: module 'app.merge_service' has no attribute 'merge_disease'`.

- [ ] **Step 3: Implement**

In `app/merge_service.py`:

1. Replace the imports block with:

```python
import logging
from dataclasses import dataclass, field

from .diff_service import FIELDS
from .ontology_service import OntologyService
from .schema import CATEGORIES
```

2. After `CHANGELOG = "ARI_ChangeLog"` add:

```python
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"


def _multi_valued(base: str) -> frozenset:
    """Predicates whose values are a set, merged value by value.

    Everything else is single-valued: a label, a definition, an item's
    description. Named rather than guessed from the data, because a synonym
    list that happens to hold one value is still a list.
    """
    suffixes = {suffix for kind, suffix, _ in OntologyService.EDITABLE.values()
                if kind == "multi_ann"}
    suffixes |= {CHANGELOG, "ARI_EnrichmentSource", "hasParentDisease",
                 "hasParentCategory", *ITEM_LINKS}
    return frozenset({base + s for s in suffixes} | {RDF_TYPE, RDFS + "seeAlso"})


def _field_labels(base: str) -> dict:
    """Display label for each predicate a curator can edit."""
    names = dict(FIELDS)
    out = {RDFS + "label": "Label", RDFS + "comment": "Definition", RDFS + "seeAlso": "HPO id"}
    for key, (_, suffix, _) in OntologyService.EDITABLE.items():
        if suffix:
            out[base + suffix] = names.get(key, key)
    for spec in CATEGORIES.values():
        for f in spec["fields"]:
            if f["kind"] == "data":
                out.setdefault(base + f["key"], f["label"])
    return out
```

3. Replace `_ensure_property` and `_graft` with:

```python
def _ensure_property(dst, p_iri, sources):
    """Declare ``p_iri`` in ``dst`` if it has never seen it.

    A curator's session can be the first to use an annotation property —
    ``ARI_EnrichmentSource`` was introduced exactly that way — and a property
    used but never declared makes the published file fail its own schema check.
    """
    if not p_iri.startswith(dst.base) or dst.world._abbreviate(p_iri, False) is not None:
        return                             # rdf/rdfs/owl need no declaration; or already known
    src = next(s for s in sources if s.world._abbreviate(p_iri, False) is not None)
    s_src = src.world._abbreviate(p_iri, False)
    s_dst = dst.world._abbreviate(p_iri)
    for p, o in list(src.world._get_obj_triples_s_po(s_src)):
        dst.onto._add_obj_triple_spo(s_dst, dst.world._abbreviate(src.world._unabbreviate(p)),
                                     dst.world._abbreviate(src.world._unabbreviate(o)))


def _triples(svc, iri):
    """Everything ``svc`` says about ``iri`` as ``{predicate: {value}}``, or None.

    Values are world-independent: object values are IRIs, data values carry
    their datatype IRI (or language tag), so two services compare directly.
    """
    w = svc.world
    s = w._abbreviate(iri, False)
    if s is None:
        return None
    out = {}
    for p, o in w._get_obj_triples_s_po(s):
        if o < 0:
            # Anonymous class expressions would need their whole subtree copied.
            # No disease or item in this ontology has one; refusing beats
            # committing a record with a piece of itself missing.
            raise ValueError(f"{iri} carries an anonymous node under {w._unabbreviate(p)}")
        out.setdefault(w._unabbreviate(p), set()).add(("o", w._unabbreviate(o)))
    for p, o, d in w._get_data_triples_s_pod(s):
        dt = w._unabbreviate(d) if isinstance(d, int) and d > 0 else d
        out.setdefault(w._unabbreviate(p), set()).add(("d", o, dt))
    return out or None


def _write(dst, iri, props, sources):
    """Replace everything ``dst`` says about ``iri`` with ``props``.

    ``None`` deletes the subject — a curator removing an item, where leaving the
    old triples behind would resurrect it.
    """
    dw = dst.world
    s = dw._abbreviate(iri)
    dst.onto._del_obj_triple_spo(s, None, None)
    dst.onto._del_data_triple_spod(s, None, None, None)
    for p_iri, values in (props or {}).items():
        _ensure_property(dst, p_iri, sources)
        p = dw._abbreviate(p_iri)
        for v in values:
            if v[0] == "o":
                dst.onto._add_obj_triple_spo(s, p, dw._abbreviate(v[1]))
            else:
                _, o, dt = v
                d = dw._abbreviate(dt) if isinstance(dt, str) and not dt.startswith("@") else dt
                dst.onto._add_data_triple_spod(s, p, o, d)


def _graft(src, dst, iri):
    """Replace everything ``dst`` says about ``iri`` with what ``src`` says."""
    _write(dst, iri, _triples(src, iri), (src,))
```

4. Delete `_changelog` and `upstream_edits` (and `Conflict`, which nothing raises — confirm with `grep -rn "merge_service.Conflict\|Conflict(" app tests` first; remove only if unused).

5. Append the merge:

```python
@dataclass
class Merge:
    """One disease merged: the triples to write per subject, and what is left to decide."""
    name: str
    subjects: dict = field(default_factory=dict)      # subject iri -> props, None = delete
    conflicts: list = field(default_factory=list)
    upstream_log: list = field(default_factory=list)


def _show(values) -> str:
    return ", ".join(sorted(str(v[1]) for v in values)) if values else "(empty)"


def _name(*props) -> str:
    for p in props:
        for v in sorted((p or {}).get(RDFS + "label", ()), key=str):
            return str(v[1])
    return ""


def _merge_subject(s, b, m, t, has_base, multi, labels, choices, conflicts):
    """The merged props for one subject; appends to ``conflicts`` what it cannot decide."""
    if m == t:
        return m
    if has_base and m == b:
        return t
    if has_base and t == b:
        return m
    if m is None or t is None:
        if not has_base:
            return m or t                  # present on one side only: without a base, keep it
        key = f"{s}|"                      # one side deleted it, the other edited it
        pick = choices.get(key)
        if pick is None:
            conflicts.append({"key": key, "label": "Whole record", "subject": _name(m, t, b),
                              "base": "present",
                              "mine": "removed" if m is None else "edited",
                              "theirs": "removed" if t is None else "edited"})
            return m
        return m if pick == "mine" else t
    props = {}
    for p in sorted(set(m) | set(t) | set(b or {})):
        B, M, T = (b or {}).get(p, set()), m.get(p, set()), t.get(p, set())
        if p in multi:
            if not has_base:
                B = M & T
            v = (B & M & T) | (M - B) | (T - B)
        elif M == T:
            v = M
        elif has_base and M == B:
            v = T
        elif has_base and T == B:
            v = M
        else:
            key = f"{s}|{p}"
            pick = choices.get(key)
            if pick is None:
                conflicts.append({"key": key, "label": labels.get(p, p.rsplit("#", 1)[-1].rsplit("/", 1)[-1]),
                                  "subject": _name(m, t, b),
                                  "base": _show(B) if has_base else None,
                                  "mine": _show(M), "theirs": _show(T)})
                v = M
            else:
                v = M if pick == "mine" else T
        if v:
            props[p] = v
    return props or None


def merge_disease(base, mine, theirs, iri, choices=None) -> Merge:
    """Three-way merge of one disease and every item individual hanging off it.

    ``base`` is the ancestor both copies descend from, or None for a working copy
    made before ancestors were recorded. Nothing is written; see :func:`apply`.
    """
    choices = choices or {}
    has_base = base is not None
    multi, labels = _multi_valued(mine.base), _field_labels(mine.base)
    sides = [x for x in (base, mine, theirs) if x is not None]
    subjects = sorted({iri}.union(*(_item_iris(x, iri) for x in sides)))
    b0, m0, t0 = ((_triples(x, iri) if x is not None else None) for x in (base, mine, theirs))
    out = Merge(name=_name(t0, m0, b0))
    clog = mine.base + CHANGELOG
    mine_log = (m0 or {}).get(clog, set())
    out.upstream_log = sorted(str(v[1]) for v in (t0 or {}).get(clog, set()) - mine_log)

    for s in subjects:
        b = _triples(base, s) if has_base else None
        out.subjects[s] = _merge_subject(s, b, _triples(mine, s), _triples(theirs, s),
                                         has_base, multi, labels, choices, out.conflicts)

    # An item's link follows the item: a conflict settled in the item's favour
    # keeps its link even where one side dropped it, and a deleted item takes
    # its link with it.
    disease = out.subjects.get(iri)
    if disease is not None:
        link_preds = {mine.base + link for link in ITEM_LINKS}
        linked = {}
        for x in (b0, m0, t0):
            for p in link_preds:
                for v in (x or {}).get(p, ()):
                    linked[v[1]] = p
        for p in link_preds:
            vals = {("o", i) for i, lp in linked.items()
                    if lp == p and out.subjects.get(i) is not None}
            if vals:
                disease[p] = vals
            else:
                disease.pop(p, None)
    return out


def apply(dst, merge: Merge, sources) -> None:
    """Write a conflict-free merge into ``dst``."""
    if merge.conflicts:
        raise ValueError(f"{merge.name} still has {len(merge.conflicts)} unresolved conflict(s)")
    for s, props in merge.subjects.items():
        _write(dst, s, props, sources)
```

Note `_item_iris` is defined above in this module already; keep it.

- [ ] **Step 4: Run the new tests and the existing graft tests**

Run: `python -m pytest tests/test_merge_service.py tests/test_publish_rebase.py -q`
Expected: `test_merge_service.py` all PASS. `test_publish_rebase.py` fails only in tests that call `upstream_edits` (removed) — that is Step 5.

If `test_without_an_ancestor_list_fields_keep_both_sides` reports a conflict, print `m.conflicts`: `update_disease` also rewrites nothing single-valued, so a conflict there means a predicate is missing from `_multi_valued` — add it there, don't loosen the test.

- [ ] **Step 5: Remove the tests of the deleted function**

In `tests/test_publish_rebase.py` delete the whole `# ---- conflicts` section (the three tests calling `upstream_edits`) and the whole `# ---- taking the branch's version` section (`_saved`, and the two `test_taking_the_branchs_version_*` tests — they exercise `/api/v2/discard`, which Task 4 removes).

Run: `python -m pytest -q`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

```bash
ruff check app tests
git add app/merge_service.py tests/test_merge_service.py tests/test_publish_rebase.py
git commit -m "Three-way merge of one disease against the source branch

Snapshots each subject's triples world-independently and merges the
ancestor, the working copy and the branch: list predicates value by
value, everything else by which side changed it, and an item deleted on
one side and edited on the other as a conflict. upstream_edits goes;
the merge reports the branch's unseen changelog lines itself.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Merging a set of diseases into a working copy and its ancestor

**Files:**
- Modify: `app/merge_service.py`
- Modify: `tests/test_merge_service.py`

**Interfaces:**
- Consumes: `merge_disease`, `apply`, `_triples`, `_item_iris`, `graft_diseases` (Task 1 / existing).
- Produces: `merge_service.merge_into(working, ancestor, theirs, iris, touched, choices=None) -> dict` returning `{"merged": [iri], "conflicts": [{"iri", "name", "upstream_log", "fields"}]}`. Mutates `working` and `ancestor` in memory; the caller saves. `merged` lists diseases whose working copy actually changed.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_merge_service.py`:

```python
# ------------------------------------------------------------- many diseases
def test_merge_into_writes_clean_diseases_and_advances_their_ancestor(trio):
    base, mine, theirs = trio
    d = _iri(mine)
    theirs.update_disease(d, {"definition": "theirs"}, editor="bob")

    out = merge_service.merge_into(mine, base, theirs, [d], touched=set())

    assert out == {"merged": [d], "conflicts": []}
    assert _reloaded(mine).get_disease_detail(d)["definition"] == "theirs"
    assert _reloaded(base).get_disease_detail(d)["definition"] == "theirs"


def test_merge_into_leaves_a_conflicting_disease_and_its_ancestor_alone(trio):
    base, mine, theirs = trio
    clash, clean = _iri(mine, 0), _iri(mine, 1)
    mine.update_disease(clash, {"definition": "mine"}, editor="ada")
    theirs.update_disease(clash, {"definition": "theirs"}, editor="bob")
    theirs.update_disease(clean, {"definition": "theirs too"}, editor="bob")
    before = base.get_disease_detail(clash)["definition"]

    out = merge_service.merge_into(mine, base, theirs, [clash, clean], touched={clash})

    assert out["merged"] == [clean]
    [c] = out["conflicts"]
    assert c["iri"] == clash and c["fields"][0]["label"] == "Definition"
    assert any("| bob |" in e for e in c["upstream_log"])
    assert _reloaded(mine).get_disease_detail(clash)["definition"] == "mine"
    assert _reloaded(base).get_disease_detail(clash)["definition"] == before


def test_merge_into_reports_nothing_for_diseases_already_equal(trio):
    base, mine, theirs = trio
    assert merge_service.merge_into(mine, base, theirs, [_iri(mine)], touched=set()) == \
        {"merged": [], "conflicts": []}


def test_without_an_ancestor_an_untouched_disease_takes_theirs(trio):
    _, mine, theirs = trio
    d = _iri(mine)
    mine.update_disease(d, {"definition": "stale mine"}, editor="ada")   # not in touched
    theirs.update_disease(d, {"definition": "theirs"}, editor="bob")

    out = merge_service.merge_into(mine, None, theirs, [d], touched=set())

    assert out["conflicts"] == []
    assert _reloaded(mine).get_disease_detail(d)["definition"] == "theirs"


def test_merge_into_applies_choices_per_disease(trio):
    base, mine, theirs = trio
    d = _iri(mine)
    mine.update_disease(d, {"definition": "mine"}, editor="ada")
    theirs.update_disease(d, {"definition": "theirs"}, editor="bob")
    key = f"{d}|http://www.w3.org/2000/01/rdf-schema#comment"

    out = merge_service.merge_into(mine, base, theirs, [d], touched={d},
                                   choices={d: {key: "mine"}})

    assert out == {"merged": [d], "conflicts": []}
    reloaded = _reloaded(mine).get_disease_detail(d)
    assert reloaded["definition"] == "mine"
    assert any("| bob |" in e for e in reloaded["changelog"])
```

Note on the last test: `merged` includes `d` because the changelog changed even though the definition is mine.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_merge_service.py -q -k merge_into`
Expected: FAIL — `no attribute 'merge_into'`.

- [ ] **Step 3: Implement** — append to `app/merge_service.py`:

```python
def _record(svc, iri) -> dict:
    """A disease and its items as triple snapshots, for equality checks."""
    return {s: _triples(svc, s) for s in sorted({iri} | _item_iris(svc, iri))}


def merge_into(working, ancestor, theirs, iris, touched, choices=None) -> dict:
    """Merge ``theirs`` into ``working`` for each disease in ``iris``.

    Every disease that merges without an open question is written into
    ``working``, and its ancestor moves up to ``theirs`` so the next merge sees
    only newer changes. A disease with a conflict is left exactly as it was, on
    both. Without an ancestor, a disease the curator has not touched simply
    takes the branch's version — there is no work of theirs in it to protect.
    """
    choices = choices or {}
    merged, conflicts, clean = [], [], []
    for iri in sorted(iris):
        if ancestor is None and iri not in touched:
            if _record(working, iri) != _record(theirs, iri):
                graft_diseases(theirs, working, {iri})
                merged.append(iri)
            continue
        m = merge_disease(ancestor, working, theirs, iri, choices.get(iri))
        if m.conflicts:
            conflicts.append({"iri": iri, "name": m.name,
                              "upstream_log": m.upstream_log, "fields": m.conflicts})
            continue
        clean.append(iri)
        # The merge's subjects are a superset of the working copy's, so
        # comparing over them is exact.
        current = _record(working, iri)
        if any(m.subjects[s] != current.get(s) for s in m.subjects):
            apply(working, m, (working, theirs))
            merged.append(iri)
    if ancestor is not None:
        stale = [i for i in clean if _record(ancestor, i) != _record(theirs, i)]
        if stale:
            graft_diseases(theirs, ancestor, stale)
    return {"merged": merged, "conflicts": conflicts}
```

- [ ] **Step 4: Run all merge tests**

Run: `python -m pytest tests/test_merge_service.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
ruff check app tests
git add app/merge_service.py tests/test_merge_service.py
git commit -m "Merge the branch into a working copy disease by disease

Clean diseases are written and their ancestor advanced; a disease with
a conflict is left untouched on both so nothing is half-merged. A copy
with no ancestor takes the branch's version of every disease the
curator has not touched.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Ancestor storage, `branch_sha`, and `workspace.merge_from`

**Files:**
- Modify: `app/workspace.py`
- Modify: `app/github_service.py`
- Modify: `app/routes/settings.py:26-40`
- Modify: `tests/test_ref_session.py` (remove the `forget` test)
- Create: `tests/test_working_copy_sync.py`

**Interfaces:**
- Consumes: `merge_service.merge_into` (Task 2).
- Produces:
  - `workspace.ancestor(login) -> OntologyService | None`
  - `workspace.ancestor_sha(login) -> str | None`
  - `workspace.set_ancestor(login, data: bytes, sha: str | None) -> None`
  - `workspace.set_ancestor_sha(login, sha: str) -> None`
  - `workspace.merge_from(login, theirs, iris, choices=None) -> dict` (same shape as `merge_into`)
  - `github_service.branch_sha(token, owner, repo, branch) -> str` (async)

- [ ] **Step 1: Write the failing tests** — create `tests/test_working_copy_sync.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_working_copy_sync.py -q`
Expected: FAIL — `module 'app.workspace' has no attribute 'ancestor'`.

- [ ] **Step 3: Implement ancestor storage in `app/workspace.py`**

Add `from . import atomic_store, config, merge_service, sessions` (extend the existing import). After `_pr_base` add:

```python
# The version each working copy started from. A three-way merge needs it to
# tell a field the curator changed from one the branch changed; without it the
# two only "differ". Kept in a subdirectory: the idle sweep treats every
# ``USER_DIR/*.owl`` as a working copy.
def _ancestor_dir() -> Path:
    return config.USER_DIR / "ancestor"


def ancestor_path(login) -> Path:
    return _ancestor_dir() / f"{login}.owl"


def ancestor(login) -> OntologyService | None:
    p = ancestor_path(login)
    return OntologyService(str(p)) if p.exists() else None


def ancestor_sha(login) -> str | None:
    return atomic_store.read_json(_ancestor_dir() / f"{login}.json", {}).get("sha")


def set_ancestor(login, data: bytes, sha):
    _ancestor_dir().mkdir(parents=True, exist_ok=True)
    atomic_store.write_bytes(ancestor_path(login), data, mode=0o644)
    atomic_store.write_json(_ancestor_dir() / f"{login}.json", {"sha": sha})


def set_ancestor_sha(login, sha):
    atomic_store.write_json(_ancestor_dir() / f"{login}.json", {"sha": sha})


def _drop_ancestor(login):
    ancestor_path(login).unlink(missing_ok=True)
    (_ancestor_dir() / f"{login}.json").unlink(missing_ok=True)
```

In `user_service`, inside `if create:` after `os.utime(f, None)` add:

```python
        # Made from the local copy of the branch, whose commit is not known
        # here: a null sha makes the first sync merge rather than skip.
        set_ancestor(login, Path(config.ONTOLOGY_FILE).read_bytes(), None)
```

In `_reset_user` add `_drop_ancestor(login)` after `_clear_ref_session(login)`. In `_sweep_user_data`, add `_drop_ancestor(login)` after `_clear_ref_session(login)`.

Delete `forget` (and its docstring). In `tests/test_ref_session.py` delete `test_forgetting_one_disease_leaves_the_rest_of_the_session`.

- [ ] **Step 4: Add `merge_from` to `app/workspace.py`** (after `_restore_working_copy`):

```python
def merge_from(login, theirs, iris, choices=None) -> dict:
    """Merge ``theirs`` into ``login``'s working copy for ``iris``, and save.

    The working copy is snapshotted first and restored if the merge refuses
    part-way. Afterwards it is evicted: the merge writes triples underneath
    owlready2's per-object cache, so the loaded copy would read back stale values.
    """
    svc = user_service(login)
    anc = ancestor(login)
    snapshot = svc.path.read_bytes()
    try:
        out = merge_service.merge_into(svc, anc, theirs, iris, touched(login), choices)
        svc._save()
        if anc is not None:
            anc._save()
    except Exception:
        _restore_working_copy(login, svc, snapshot)
        raise
    USER_SVC.pop(login, None)
    return out
```

- [ ] **Step 5: Add `branch_sha` to `app/github_service.py`** (after `get_file_at`):

```python
async def branch_sha(token: str | None, owner: str, repo: str, branch: str) -> str:
    """The commit ``branch`` points at (token optional for public repos)."""
    hdrs = {"Accept": "application/vnd.github.sha"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=20, headers=hdrs) as c:
        r = await c.get(f"{API}/repos/{owner}/{repo}/commits/{branch}")
        if r.status_code >= 300:
            raise ValueError(f"Could not read the head of {branch}: {r.status_code} {r.text[:200]}")
        return r.text.strip()
```

- [ ] **Step 6: Record the ancestor in `_fetch_branch`** (`app/routes/settings.py`). Replace the body with:

```python
    sha = await gh.branch_sha(token, config.GH_OWNER, config.GH_REPO, branch)
    data = await gh.get_file_at(token, config.GH_OWNER, config.GH_REPO,
                                config.GH_ONTOLOGY_PATH, sha)
    workspace._reset_user(login)       # verdicts and edits reference the old base
    config.USER_DIR.mkdir(parents=True, exist_ok=True)
    atomic_store.write_bytes(config.USER_DIR / f"{login}.owl", data, mode=0o644)
    workspace.set_ancestor(login, data, sha)
    workspace.USER_SVC.pop(login, None)   # reload on next use, from the file just written
    workspace._set_branch_state(login, source_branch=branch, pr_base=branch)
```

- [ ] **Step 7: Run tests**

Run: `python -m pytest -q`
Expected: all PASS. (`/api/v2/discard` still calls `workspace.forget`; it's untested now and Task 4 deletes it. If ruff flags it, carry on — Task 4 removes it in the next commit.)

- [ ] **Step 8: Commit**

```bash
ruff check app tests
git add app/workspace.py app/github_service.py app/routes/settings.py tests/test_working_copy_sync.py tests/test_ref_session.py
git commit -m "Record the version each working copy started from

The ancestor lives under .user-data/ancestor/ with the branch commit it
came from, is written on create and fetch, and is dropped with the
copy. merge_from merges the branch into a working copy, saves both and
evicts the stale owlready cache.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Sync, resolve, and publish endpoints

**Files:**
- Create: `app/routes/merge.py`
- Modify: `app/routes/__init__.py`
- Modify: `app/routes/publish.py`
- Modify: `tests/test_working_copy_sync.py`

**Interfaces:**
- Consumes: `workspace.merge_from`, `workspace.ancestor_sha`, `workspace.set_ancestor_sha`, `gh.branch_sha` (Task 3).
- Produces (HTTP):
  - `POST /api/v2/sync` → `{"up_to_date": true}` or `{"merged": [...], "conflicts": [...]}`; 502 `{"detail"}` if GitHub is unreachable.
  - `POST /api/v2/resolve` body `{"choices": {iri: {key: "mine"|"theirs"}}}` → `{"merged": [...]}` or 409 `{"detail", "conflicts"}`.
  - `POST /api/v2/publish` → 409 `{"detail", "conflicts"}` (field-level) where it used to return `upstream_edits` collisions.
  - `POST /api/v2/discard` removed.

- [ ] **Step 1: Write the failing route tests** — append to `tests/test_working_copy_sync.py`:

```python
# ---------------------------------------------------------------- endpoints
from fastapi.testclient import TestClient

import app.main as main
from app import github_service as gh
from app import sessions

client = TestClient(main.app)
COMMENT = "http://www.w3.org/2000/01/rdf-schema#comment"


@pytest.fixture
def branch(curator, monkeypatch, make_service):
    """Signed in as ada, with GitHub stubbed to serve ``branch['svc']`` at ``branch['sha']``."""
    state = {"svc": make_service(), "sha": "sha-2"}
    monkeypatch.setattr(config, "GH_ENABLED", True)
    monkeypatch.setattr(sessions, "_user", lambda r: {"token": "t", "identity": {"login": "ada"}})
    monkeypatch.setattr(sessions, "_login", lambda r: "ada")

    async def _sha(*a):
        return state["sha"]

    async def _file(*a):
        state["svc"]._save()
        return state["svc"].path.read_bytes()

    monkeypatch.setattr(gh, "branch_sha", _sha)
    monkeypatch.setattr(gh, "get_file_at", _file)
    return state


def test_sync_is_a_no_op_when_the_branch_has_not_moved(branch):
    workspace.user_service("ada", create=True)
    workspace.set_ancestor_sha("ada", "sha-2")

    assert client.post("/api/v2/sync").json() == {"up_to_date": True}


def test_sync_merges_the_branch_and_records_its_commit(branch):
    d = _first(workspace.user_service("ada", create=True))
    branch["svc"].update_disease(d, {"definition": "theirs"}, editor="bob")

    r = client.post("/api/v2/sync").json()

    assert r["merged"] == [d] and r["conflicts"] == []
    assert workspace.ancestor_sha("ada") == "sha-2"
    assert workspace.user_service("ada").get_disease_detail(d)["definition"] == "theirs"


def test_sync_reports_conflicts_and_does_not_advance(branch):
    svc = workspace.user_service("ada", create=True)
    d = _first(svc)
    svc.update_disease(d, {"definition": "mine"}, editor="ada")
    workspace.mark("ada", d)
    branch["svc"].update_disease(d, {"definition": "theirs"}, editor="bob")

    r = client.post("/api/v2/sync").json()

    assert [c["iri"] for c in r["conflicts"]] == [d]
    assert workspace.ancestor_sha("ada") is None


def test_resolve_applies_the_choices(branch):
    svc = workspace.user_service("ada", create=True)
    d = _first(svc)
    svc.update_disease(d, {"definition": "mine"}, editor="ada")
    branch["svc"].update_disease(d, {"definition": "theirs"}, editor="bob")

    r = client.post("/api/v2/resolve", json={"choices": {d: {f"{d}|{COMMENT}": "theirs"}}})

    assert r.status_code == 200 and r.json()["merged"] == [d]
    assert workspace.user_service("ada").get_disease_detail(d)["definition"] == "theirs"


def test_resolve_with_an_unanswered_conflict_hands_it_back(branch):
    svc = workspace.user_service("ada", create=True)
    d = _first(svc)
    svc.update_disease(d, {"definition": "mine"}, editor="ada")
    branch["svc"].update_disease(d, {"definition": "theirs"}, editor="bob")

    r = client.post("/api/v2/resolve", json={"choices": {d: {}}})

    assert r.status_code == 409
    assert r.json()["conflicts"][0]["fields"][0]["key"] == f"{d}|{COMMENT}"


def test_resolve_rejects_a_choice_that_is_not_mine_or_theirs(branch):
    workspace.user_service("ada", create=True)
    r = client.post("/api/v2/resolve", json={"choices": {"x": {"k": "both"}}})
    assert r.status_code == 400


def test_publish_refuses_with_field_level_conflicts(branch):
    svc = workspace.user_service("ada", create=True)
    d = _first(svc)
    svc.update_disease(d, {"definition": "mine"}, editor="ada")
    workspace.mark("ada", d)
    branch["svc"].update_disease(d, {"definition": "theirs"}, editor="bob")

    r = client.post("/api/v2/publish", json={})

    assert r.status_code == 409
    [c] = r.json()["conflicts"]
    assert c["iri"] == d and c["fields"][0]["label"] == "Definition"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_working_copy_sync.py -q`
Expected: the new endpoint tests FAIL with 404 (sync/resolve) and publish returning the old collision payload without `fields`.

- [ ] **Step 3: Let `_baseline_service` take a ref** (`app/routes/publish.py`):

```python
async def _baseline_service(request, u, ref=None):
    """The source branch's ontology (or ``ref`` of it), as a service. Raises if it cannot be read.

    The pending-changes list and the pull-request summary compare against this,
    and the publish itself is built on top of it. The caller is responsible for
    deleting ``service.path``.
    """
    import tempfile
    data = await gh.get_file_at(u["token"], config.GH_OWNER, config.GH_REPO,
                                config.GH_ONTOLOGY_PATH, ref or workspace._source_branch(request))
    tf = tempfile.NamedTemporaryFile(suffix=".owl", delete=False)
    tf.write(data)
    tf.close()
    return OntologyService(tf.name)
```

- [ ] **Step 4: Create `app/routes/merge.py`**

```python
"""Keeping a curator's working copy current with the source branch.

``sync`` merges whatever the branch gained since the copy's ancestor; ``resolve``
applies the curator's per-field choices where both sides changed the same thing.
Publish runs the same merge before it commits (see ``publish.py``).
"""
import logging

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from .. import config, sessions, workspace
from .. import github_service as gh
from .publish import _baseline_service, _discard

log = logging.getLogger(__name__)

router = APIRouter()


def _signed_in(request):
    if not config.GH_ENABLED:
        raise ValueError("GitHub integration is not configured")
    return sessions._user(request)


def _all_diseases(*services) -> set:
    return {d["iri"] for s in services for d in s.get_diseases_list()}


@router.post("/api/v2/sync")
async def sync(request: Request):
    """Merge the source branch into this curator's working copy."""
    u = _signed_in(request)
    if not u:
        return JSONResponse(status_code=401, content={"detail": "Sign in with GitHub first"})
    login = u["identity"]["login"]
    if not (config.USER_DIR / f"{login}.owl").exists():
        return {"up_to_date": True}               # nothing of theirs to bring up to date
    branch = workspace._source_branch(request)
    try:
        sha = await gh.branch_sha(u["token"], config.GH_OWNER, config.GH_REPO, branch)
        if sha == workspace.ancestor_sha(login):
            return {"up_to_date": True}
        theirs = await _baseline_service(request, u, ref=sha)
    except Exception as e:
        log.warning("Could not check %s for updates for @%s: %s", branch, login, e)
        return JSONResponse(status_code=502, content={
            "detail": f"Couldn't check {branch} for updates. Your working copy is unchanged."})
    try:
        out = workspace.merge_from(login, theirs,
                                   _all_diseases(theirs, workspace.user_service(login)))
    finally:
        _discard(theirs.path)
    if not out["conflicts"]:
        workspace.set_ancestor_sha(login, sha)
    log.info("Synced @%s with %s@%s: %d merged, %d in conflict", login, branch, sha[:7],
             len(out["merged"]), len(out["conflicts"]))
    return out


@router.post("/api/v2/resolve")
async def resolve(request: Request, payload: dict = Body(...)):
    """Apply the curator's choices where both sides changed the same field."""
    u = _signed_in(request)
    if not u:
        return JSONResponse(status_code=401, content={"detail": "Sign in with GitHub first"})
    choices = payload.get("choices")
    if not isinstance(choices, dict) or not all(
            isinstance(c, dict) and all(v in ("mine", "theirs") for v in c.values())
            for c in choices.values()):
        return JSONResponse(status_code=400, content={
            "detail": "choices must map each disease to {field: 'mine' | 'theirs'}"})
    login = u["identity"]["login"]
    try:
        theirs = await _baseline_service(request, u)
    except Exception as e:
        log.error("Could not read the source branch to resolve for @%s: %s", login, e)
        return JSONResponse(status_code=502, content={
            "detail": "Could not read the source branch. Nothing was changed — try again."})
    try:
        out = workspace.merge_from(login, theirs, set(choices), choices)
    finally:
        _discard(theirs.path)
    if out["conflicts"]:
        return JSONResponse(status_code=409, content={
            "detail": "Some fields still need a choice.", "conflicts": out["conflicts"]})
    return {"merged": out["merged"]}
```

Register it in `app/routes/__init__.py`: add `merge` to the import and `merge.router,` after `publish.router,`.

- [ ] **Step 5: Merge in publish, remove discard** (`app/routes/publish.py`)

Delete the whole `discard_diseases` endpoint (decorator through `return {"ok": True, "discarded": len(iris)}`).

In `publish`, replace

```python
    rollback = None
    try:
        collisions = merge_service.upstream_edits(svc, baseline, scope)
        if collisions:
            return JSONResponse(status_code=409, content={
                ...
                "conflicts": collisions})

        enrich_note = ""
```

with

```python
    # Bring the curator's diseases up to date with the branch before anything
    # is written: whatever merges cleanly is folded in, and a field both sides
    # changed goes back to the curator to choose.
    try:
        merged = workspace.merge_from(login, baseline, scope)
    except Exception:
        _discard(baseline.path)
        raise
    if merged["conflicts"]:
        _discard(baseline.path)
        return JSONResponse(status_code=409, content={
            "detail": "These diseases changed on " + source_branch + " in the same fields "
                      "you edited: " + ", ".join(c["name"] for c in merged["conflicts"]) +
                      ". Choose which version of each to keep, then submit again.",
            "conflicts": merged["conflicts"]})
    # merge_from evicted the loaded copy; resolve it again from the merged file.
    svc = workspace.service_for(request, write=True) if any_review else workspace.service_for(request)

    rollback = None
    try:
        enrich_note = ""
```

`workspace.merge_from` calls `workspace.user_service(login)`, so the working copy must exist: it does — `svc` was resolved above with `write=True` when `any_review`, and otherwise `scope` is the non-empty touched set, which only exists alongside a working copy.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: all PASS, including `tests/test_write_auth.py` (it derives write routes from OpenAPI, so `/api/v2/sync` and `/api/v2/resolve` are checked for a 401 automatically).

- [ ] **Step 7: Commit**

```bash
ruff check app tests
git add app/routes/merge.py app/routes/__init__.py app/routes/publish.py tests/test_working_copy_sync.py
git commit -m "Sync, resolve and publish through the three-way merge

POST /api/v2/sync merges the branch into the working copy when its head
has moved; POST /api/v2/resolve applies per-field choices; publish
merges its scope first and refuses with field-level conflicts instead
of disease names. /api/v2/discard goes: choosing theirs throughout is
the same operation without dropping review verdicts.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The choice dialog and both pages

**Files:**
- Modify: `static/js/ui-dialog.js`
- Modify: `static/css/ui-dialog.css`
- Modify: `static/js/github.js`
- Modify: `static/ref-edits/ref-edits.js`

**Interfaces:**
- Consumes: the HTTP API from Task 4.
- Produces: `UIDialog.merge(conflicts) -> Promise<{[iri]: {[key]: 'mine'|'theirs'}} | null>`.

- [ ] **Step 1: Add `UIDialog.merge`** to `static/js/ui-dialog.js`, before `showFieldErrors`:

```js
  // ------------------------------------------------------------------ merge
  // Where the curator and the source branch both changed the same field, the
  // curator chooses. Everything else has already been combined by the server.
  // Resolves to {iri: {key: 'mine'|'theirs'}}, or null when put off.
  function mergeDialog(conflicts) {
    return new Promise(resolve => {
      const sections = conflicts.map((c, ci) => `
        <section class="merge-disease" data-iri="${esc(c.iri)}">
          <h3 class="merge-name">${esc(c.name)}</h3>
          ${c.upstream_log.length ? `<details class="merge-log" open>
            <summary>What changed on the branch</summary>
            <ul>${c.upstream_log.map(l => `<li>${esc(l)}</li>`).join('')}</ul></details>` : ''}
          <div class="merge-all">
            <button type="button" class="ui-btn" data-all="mine">Keep all mine</button>
            <button type="button" class="ui-btn" data-all="theirs">Keep all theirs</button>
          </div>
          ${c.fields.map((f, fi) => `
            <fieldset class="merge-field">
              <legend>${f.subject && f.subject !== c.name ? esc(f.subject) + ' · ' : ''}${esc(f.label)}</legend>
              <label><input type="radio" name="m${ci}_${fi}" value="mine" data-key="${esc(f.key)}">
                <span class="merge-side">Yours</span><span class="merge-val">${esc(f.mine)}</span></label>
              <label><input type="radio" name="m${ci}_${fi}" value="theirs" data-key="${esc(f.key)}">
                <span class="merge-side">Theirs</span><span class="merge-val">${esc(f.theirs)}</span></label>
            </fieldset>`).join('')}
        </section>`).join('');
      const one = conflicts.length === 1;
      const dlg = el(`<dialog class="ui-dialog merge-dialog">
        <form method="dialog" class="ui-dialog-form">
          <h2 class="ui-dialog-title">${one ? 'A disease' : conflicts.length + ' diseases'} changed on both sides</h2>
          <p class="ui-dialog-detail">For each field, choose which version to keep. Everything else has already been combined, and your review verdicts are kept either way.</p>
          <div class="merge-body">${sections}</div>
          <div class="ui-dialog-actions">
            <button value="cancel" class="ui-btn">Leave it for now</button>
            <button value="ok" class="ui-btn primary" disabled>Apply</button>
          </div>
        </form>
      </dialog>`);
      const applyBtn = dlg.querySelector('button[value="ok"]');
      const refresh = () => {
        const names = new Set([...dlg.querySelectorAll('input[type=radio]')].map(r => r.name));
        applyBtn.disabled = [...names].some(n => !dlg.querySelector(`input[name="${n}"]:checked`));
      };
      dlg.addEventListener('change', refresh);
      dlg.querySelectorAll('[data-all]').forEach(b => b.addEventListener('click', () => {
        b.closest('.merge-disease').querySelectorAll(`input[value="${b.dataset.all}"]`)
          .forEach(r => { r.checked = true; });
        refresh();
      }));
      document.body.appendChild(dlg);
      wireDialog(dlg, resolve, outcome => {
        if (outcome !== 'ok') return null;
        const out = {};
        dlg.querySelectorAll('.merge-disease').forEach(sec => {
          const picks = {};
          sec.querySelectorAll('input[type=radio]:checked').forEach(r => { picks[r.dataset.key] = r.value; });
          out[sec.dataset.iri] = picks;
        });
        return out;
      });
      dlg.showModal();
      dlg.querySelector('button[value="cancel"]').focus();
    });
  }
```

Add `merge: mergeDialog,` to the `root.UIDialog = { ... }` export.

- [ ] **Step 2: Style it** — append to `static/css/ui-dialog.css`:

```css
/* Per-field choice between the curator's version and the branch's. */
.merge-dialog { width: min(760px, calc(100vw - 32px)); }
.merge-body { max-height: 60vh; overflow-y: auto; display: flex; flex-direction: column; gap: 18px; }
.merge-name { font-size: 14px; font-weight: 600; margin: 0 0 6px; }
.merge-log { font-size: 12.5px; color: var(--mute, #5C6675); margin: 0 0 8px; }
.merge-log ul { margin: 4px 0 0; padding-left: 18px; }
.merge-all { display: flex; gap: 8px; margin-bottom: 8px; }
.merge-field { border: 1px solid var(--line, #D8DEE6); border-radius: 6px; padding: 8px 10px; margin: 0 0 8px; }
.merge-field legend { font-size: 12.5px; font-weight: 600; padding: 0 4px; }
.merge-field label { display: grid; grid-template-columns: auto 56px 1fr; gap: 8px; align-items: start; padding: 4px 0; font-size: 13px; cursor: pointer; }
.merge-side { font-weight: 600; }
.merge-val { white-space: pre-wrap; overflow-wrap: anywhere; }
.sync-banner { position: fixed; bottom: 16px; left: 50%; transform: translateX(-50%); z-index: 50;
  display: flex; gap: 10px; align-items: center; padding: 10px 14px; border-radius: 8px;
  background: var(--panel, #fff); border: 1px solid var(--line, #D8DEE6);
  box-shadow: 0 4px 16px rgba(15, 40, 64, .18); font-size: 13px; }
```

Before writing, `grep -n "\-\-line\|\-\-panel\|\-\-mute" static/css/*.css` and use the variable names the stylesheets actually define (keep the fallbacks).

- [ ] **Step 3: Editor page** (`static/js/github.js`)

Replace `takeTheirs` with:

```js
  // Where this curator and the source branch both changed a field, ask which
  // version to keep, and apply the answers. True once nothing is left to decide.
  async function resolveConflicts(conflicts) {
    let pending = conflicts;
    for (;;) {
      const choices = await UIDialog.merge(pending);
      if (!choices) return false;
      try {
        await api('/api/v2/resolve', { method: 'POST', body: { choices } });
        return true;
      } catch (e) {
        if (e.status === 409 && e.data && e.data.conflicts) { pending = e.data.conflicts; continue; }
        toastError(explainError(e, 'Could not apply your choices'));
        return false;
      }
    }
  }

  // Bring the working copy up to date with the source branch. Anything that
  // merges cleanly is folded in and the page reloads onto it; a field both sides
  // changed waits behind a banner until the curator chooses.
  async function syncWorkingCopy() {
    let r;
    try { r = await api('/api/v2/sync', { method: 'POST' }); }
    catch (e) { toastError(explainError(e, "Couldn't check for updates")); return; }
    if (r.up_to_date) return;
    if (r.conflicts.length) showSyncBanner(r.conflicts);
    else if (r.merged.length) location.reload();
  }

  function showSyncBanner(conflicts) {
    const n = conflicts.length;
    const b = el(`<div class="sync-banner" role="status">${n === 1 ? 'A record' : n + ' records'} changed on the source branch in fields you edited.
      <button class="hbtn primary">Choose versions</button></div>`);
    b.querySelector('button').addEventListener('click', async () => {
      if (await resolveConflicts(conflicts)) location.reload();
    });
    document.body.appendChild(b);
  }
```

In `publish()`'s catch, replace the `takeTheirs` line with:

```js
        if (e.status === 409 && e.data && e.data.conflicts) {
          if (await resolveConflicts(e.data.conflicts)) $('#pub-go').click();
          return;
        }
```

and in the success branch, after `close();`, add a reload of the open record so the form never shows pre-merge values:

```js
        if (state.activeIri) selectDisease(state.activeIri, { history: false });
```

In `refresh()`, after `if (!me.github_enabled) return;` add:

```js
    if (me.authenticated) syncWorkingCopy();
```

- [ ] **Step 4: Review page** (`static/ref-edits/ref-edits.js`)

Replace `takeTheirs` with the same `resolveConflicts` (using this page's `api('resolve', …)` and `note(… , 'error')`):

```js
  async function resolveConflicts(conflicts) {
    let pending = conflicts;
    for (;;) {
      const choices = await UIDialog.merge(pending);
      if (!choices) return false;
      try {
        await api('resolve', { method: 'POST', body: { choices } });
        return true;
      } catch (e) {
        if (e.status === 409 && e.data && e.data.conflicts) { pending = e.data.conflicts; continue; }
        note('Could not apply your choices: ' + e.message, 'error');
        return false;
      }
    }
  }

  async function syncWorkingCopy() {
    let r;
    try { r = await api('sync', { method: 'POST' }); }
    catch (e) { note("Couldn't check for updates: " + e.message, 'error'); return; }
    if (r.up_to_date || !r.conflicts.length) return;
    const n = r.conflicts.length;
    const b = document.createElement('div');
    b.className = 'sync-banner';
    b.setAttribute('role', 'status');
    b.innerHTML = `${n === 1 ? 'A disease' : n + ' diseases'} changed on the source branch in fields you edited. ` +
                  '<button class="btn">Choose versions</button>';
    b.querySelector('button').addEventListener('click', async () => {
      if (await resolveConflicts(r.conflicts)) location.reload();
    });
    document.body.appendChild(b);
  }
```

In `publish(newPr)`'s catch, replace the `takeTheirs` line with:

```js
      if (e.status === 409 && e.data && e.data.conflicts) {
        if (await resolveConflicts(e.data.conflicts)) {
          ROWS = await api('xrefs');           // merged values, before the retry reads them
          renderMatrix();
          return publish(newPr);
        }
        return;
      }
```

In `init()`, right after the `$('#pref-fetch').disabled = …` line, add:

```js
    // Merge the branch in before the matrix loads, so it shows current data.
    if (me.authenticated) await syncWorkingCopy();
```

Check `grep -n "takeTheirs\|api('discard'\|/api/v2/discard" static` returns nothing.

- [ ] **Step 5: Run the JS tests and suite**

Run: `node --test tests/ && python -m pytest -q`
Expected: all PASS.

- [ ] **Step 6: Live check in the browser**

Follow the memory note on auth-gated UI: GitHub OAuth is off locally, so drive the endpoints through a scratchpad harness. Write `<scratchpad>/merge_harness.py` that monkeypatches `config.GH_ENABLED=True`, `sessions._user`/`_login` → `ada`, `config.USER_DIR` → a scratchpad dir, and `gh.branch_sha`/`gh.get_file_at` → serve a scratchpad copy of `ontologies/ari_t1d.owl` in which Cutaneous lupus erythematosus (`ARI:0001076`) has its discoid-lupus synonyms withdrawn and a `2026-09-07 | Claude | Synonym review: …` changelog line (as ARI PR #84 did) and, in a second scenario, its definition changed. Then `uvicorn.run(main.app, port=8011)`. Add a `.claude/launch.json` entry for it and start it with `preview_start`.

Verify, recording evidence:
1. Scenario 1 (branch changed synonyms only): open `/ref-edits/`, confirm one xref on Cutaneous lupus erythematosus, submit with `gh.publish_file` stubbed to return `{"pr_number": 1, "pr_url": "…"}` → no dialog; the working copy has PR #84's synonym withdrawal and the verdict published.
2. Scenario 2 (both changed the definition): the sync banner appears; *Choose versions* opens the dialog showing the upstream log line; *Apply* is disabled until chosen; *Keep all theirs* → Apply → reload shows their definition; the verdict is still in the session.
3. `read_console_messages` shows no errors; take a screenshot of the dialog.

Delete the harness and launch entry afterwards (they live in the scratchpad; do not commit them).

- [ ] **Step 7: Commit**

```bash
git add static/js/ui-dialog.js static/css/ui-dialog.css static/js/github.js static/ref-edits/ref-edits.js
git commit -m "Choose per field where the branch and the curator both changed it

UIDialog.merge shows each conflicting field with both values and the
branch's changelog lines, so the curator can see who changed it and
why. Both pages sync at load, show a banner for anything that needs a
choice, and resolve a refused submission and retry it instead of
dropping the curator's work on the disease.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Docs and PR

**Files:**
- Modify: `README.md` (API table near line 413, and any prose about *Take their version* / discard — `grep -n "their version\|discard" README.md`)
- Modify: `changelog.md` (new section at the top)

- [ ] **Step 1: README** — in the API table add rows after `/api/v2/fetch`:

```markdown
| POST | `/api/v2/sync` | Merge the source branch into the curator's working copy (three-way, against the version it started from) |
| POST | `/api/v2/resolve` | Apply the curator's per-field choices where both sides changed the same field |
```

Remove any `/api/v2/discard` row, and replace prose describing *Take their version* with a sentence on the choice dialog.

- [ ] **Step 2: changelog.md** — add at the top, matching the existing section style:

```markdown
## working-copy-merge

Submitting from the review page was refused with *"someone else has edited it since your copy was made"* and the only way out dropped the curator's work on that disease. The someone was ARI PR #84 — the synonym review merged straight into `main` on 2026-09-07 — and the cause was structural: a working copy was snapshotted once and never caught up with the branch, so every edit made on `main` outside the app refused every later submission of the diseases it touched.

- **Each working copy records the version it started from** (`.user-data/ancestor/<login>.owl` and the commit it came from), written on create and fetch.
- **The branch is merged in, not swapped in.** `merge_service.merge_disease` compares ancestor, working copy and branch triple by triple: list fields (synonyms, subtypes, ids, changelog, item links) combine value by value; a single-valued field takes whichever side changed it; an item deleted on one side and edited on the other is a question. Submitted-but-unmerged work survives, because it is simply the working copy's side.
- **`POST /api/v2/sync` runs it at page load** when the branch head has moved; `publish` runs it before committing.
- **Where both sides changed the same field, the curator chooses.** A dialog shows both values and the branch's changelog lines — who changed it and why — with *Keep all mine* / *Keep all theirs*. `POST /api/v2/resolve` applies the answers. Review verdicts are never dropped.
- **`POST /api/v2/discard` is gone**, and with it `merge_service.upstream_edits` and `workspace.forget`: choosing *theirs* throughout is the same operation.
- Copies made before this have no ancestor: untouched diseases take the branch's version, and touched ones ask about every differing single-valued field.
```

Append the verification line with the actual pytest / node counts from the final run.

- [ ] **Step 3: Final checks**

Run: `python -m pytest -q && node --test tests/ && ruff check app tests`
Expected: all PASS / clean.

- [ ] **Step 4: Commit, push, open the PR**

```bash
git add README.md changelog.md
git commit -m "Document the working-copy merge

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin claude/ari-edits-submit-conflict-f23669
```

Check open issues first (`gh issue list --state open`) for one this resolves; add `Fixes #N` if so. Open the PR against `main` with a body summarising the changelog section, the live-check evidence, and ending with:

```
🤖 Generated with [Claude Code](https://claude.com/claude-code)
```
