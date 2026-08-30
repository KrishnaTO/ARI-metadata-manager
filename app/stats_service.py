"""What the curation effort has actually produced.

`assignments/assignments.log` was the only trace of curation, gitignored on a
single host, so nothing could answer the questions any decision about this tool
depends on: how much each curator confirms and when, which target databases
stall, where reviews are abandoned, how much work is sitting waiting for its
second pair of eyes (issue #124).

Everything here is derived from stores that already exist — the published SSSOM
mapping set, the id-authorship ledger, the assignment store and the ontology's
own cross-references. Nothing new is recorded, and nothing here touches the
network: the numbers are a projection of files already on disk, so the dashboard
loads instantly and works offline.

What is deliberately **not** here: the share of submissions that ever merge.
That needs GitHub's pull-request list, which would put a network round trip (and
an auth requirement) on every dashboard load for a number GitHub's own PR list
already shows.
"""
from __future__ import annotations

import datetime
from collections import Counter, defaultdict

from .xref_registry import XREF_DATABASES

# The review columns, in registry order — the same set the review grid shows.
REVIEW_DBS = [d for d in XREF_DATABASES if d.get("review")]


def _week(date_text: str) -> str:
    """ISO year-week for a ``YYYY-MM-DD`` mapping date; "" when unparseable."""
    try:
        y, w, _ = datetime.date.fromisoformat(str(date_text)[:10]).isocalendar()
        return f"{y}-W{w:02d}"
    except (ValueError, TypeError):
        return ""


def _judgment_index(judgments: list[dict]) -> dict[tuple[str, str], dict]:
    """``(ari_id, id) -> judgment`` for the current state of every judged cell."""
    out = {}
    for j in judgments:
        out[(j.get("ari_id"), str(j.get("id")))] = j
    return out


def coverage(rows: list[dict], judgments: list[dict]) -> list[dict]:
    """Per database: how many diseases have an id, and how far it has been judged.

    A database where ids are on file but few are confirmed is one that is
    stalling — which is not visible from the grid, because a curator only ever
    sees one screen of it at a time.
    """
    judged = _judgment_index(judgments)
    absent_ids = {j.get("ari_id") for j in judgments if j.get("judgment") == "absent"}
    out = []
    for db in REVIEW_DBS:
        key = db["key"]
        with_id = confirmed = flagged = unjudged = 0
        for r in rows:
            ids = [str(i) for i in (r.get(key) or [])]
            if not ids:
                continue
            with_id += 1
            states = {(judged.get((r.get("ari_id"), i)) or {}).get("judgment") for i in ids}
            if states == {"positive"}:
                confirmed += 1
            elif "negative" in states:
                flagged += 1
            else:
                unjudged += 1
        out.append({
            "key": key, "label": db.get("label", key),
            "diseases": len(rows), "with_id": with_id,
            "confirmed": confirmed, "flagged": flagged, "unjudged": unjudged,
            "no_term": sum(1 for r in rows if r.get("ari_id") in absent_ids and not r.get(key)),
            "blank": len(rows) - with_id,
        })
    return out


def by_curator(judgments: list[dict], authors: dict) -> list[dict]:
    """Per curator: judgments recorded, ids contributed, and the span of activity.

    ``authors`` is the id-authorship ledger (``"<iri>|<db>|<id>" -> login``);
    adding an id and judging one are different contributions and are counted
    separately, because the two-person rule means they are never the same person.
    """
    stats: dict[str, dict] = defaultdict(
        lambda: {"confirmed": 0, "flagged": 0, "no_term": 0, "ids_added": 0,
                 "first": "", "last": ""})
    for j in judgments:
        who = (j.get("author") or "").strip() or "unattributed"
        s = stats[who]
        verdict = j.get("judgment")
        s["confirmed" if verdict == "positive" else
          "flagged" if verdict == "negative" else "no_term"] += 1
        date = str(j.get("date") or "")[:10]
        if date:
            s["first"] = min(s["first"] or date, date)
            s["last"] = max(s["last"], date)
    for login in authors.values():
        stats[f"github:{login}"]["ids_added"] += 1
    return sorted(({"curator": k, **v} for k, v in stats.items()),
                  key=lambda s: -(s["confirmed"] + s["flagged"] + s["no_term"]))


def by_week(judgments: list[dict], weeks: int = 12) -> list[dict]:
    """Judgments per ISO week, most recent ``weeks`` — the throughput trend."""
    counts: dict[str, Counter] = defaultdict(Counter)
    for j in judgments:
        w = _week(j.get("date"))
        if not w:
            continue
        verdict = j.get("judgment")
        counts[w]["confirmed" if verdict == "positive" else
                  "flagged" if verdict == "negative" else "no_term"] += 1
    ordered = sorted(counts)[-weeks:]
    return [{"week": w, "confirmed": counts[w]["confirmed"],
             "flagged": counts[w]["flagged"], "no_term": counts[w]["no_term"]}
            for w in ordered]


def waiting(rows: list[dict], judgments: list[dict], authors: dict) -> dict:
    """Work that is on file and has stopped moving.

    ``needs_second_reviewer`` is the one the two-person rule creates: an id whose
    author cannot confirm it, so it sits until another curator happens to open
    that row. It is the population the review queue's own scope filters on.
    """
    judged = _judgment_index(judgments)
    unjudged = 0
    needs_second: dict[str, int] = Counter()
    for r in rows:
        for db in REVIEW_DBS:
            for ident in (r.get(db["key"]) or []):
                if (r.get("ari_id"), str(ident)) in judged:
                    continue
                unjudged += 1
                adder = authors.get(f"{r.get('iri')}|{db['key']}|{ident}")
                if adder:
                    needs_second[adder] += 1
    return {
        "ids_unjudged": unjudged,
        "needs_second_reviewer": sum(needs_second.values()),
        "by_adder": [{"curator": k, "ids": v} for k, v in needs_second.most_common()],
    }


def queues(assignees: dict) -> dict:
    """Per-curator review queues: how much is claimed and how much is finished.

    ``assignees`` is the assignment store's own shape,
    ``login -> {iris: [...], done: [...], note, updated}``.
    """
    out = []
    for login, entry in sorted(assignees.items()):
        iris = (entry or {}).get("iris") or []
        done = (entry or {}).get("done") or []
        out.append({"curator": login, "assigned": len(iris), "done": len(done),
                    "updated": (entry or {}).get("updated") or ""})
    return {"curators": out,
            "assigned": sum(c["assigned"] for c in out),
            "done": sum(c["done"] for c in out)}


def build(rows: list[dict], judgments: list[dict], authors: dict, assignees: dict) -> dict:
    """The whole picture, from stores the caller has already loaded."""
    return {
        "generated": datetime.datetime.now(datetime.timezone.utc)
                     .strftime("%Y-%m-%d %H:%M:%S UTC"),
        "diseases": len(rows),
        "judgments": len(judgments),
        "coverage": coverage(rows, judgments),
        "curators": by_curator(judgments, authors),
        "weeks": by_week(judgments),
        "waiting": waiting(rows, judgments, authors),
        "queues": queues(assignees),
    }
