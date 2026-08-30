"""Publishing a curator's work to GitHub as a pull request.

One endpoint, and the guards around it: the replay log that stops a lost
response turning into a second commit, the author id that lands in the published
SSSOM, and the snapshot that rolls the working copy back if any of the GitHub
calls fail.
"""
import logging
from pathlib import Path

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from .. import atomic_store, config, diff_service, sessions, sssom_service, stores, workspace
from .. import github_service as gh
from ..ontology_service import OntologyService

log = logging.getLogger(__name__)

router = APIRouter()

PUBLISH_KEEP = 20        # recent publishes remembered per curator


def _publish_log_path(login) -> Path:
    return config.USER_DIR / f"{login}.publishes.json"


def _remembered_publish(login, request_id):
    """The result of a publish already completed under ``request_id``.

    If the commit and PR succeed but the *response* is lost — a proxy timeout, a
    closed laptop, a flaky connection — the client shows "Publish failed", the
    curator publishes again, and a second commit lands carrying the same
    judgments. Nothing on either side detected the repeat.
    """
    if not request_id:
        return None
    return atomic_store.read_json(_publish_log_path(login), {}).get(request_id)


def _remember_publish(login, request_id, result):
    if not request_id:
        return
    done = atomic_store.read_json(_publish_log_path(login), {})
    done[request_id] = result
    # Keep the tail; this is a short-window replay guard, not an audit trail.
    if len(done) > PUBLISH_KEEP:
        done = dict(list(done.items())[-PUBLISH_KEEP:])
    atomic_store.write_json(_publish_log_path(login), done)


def _mapping_author(supplied, login: str) -> str:
    """The ``author_id`` for this curator's mappings.

    Defaults to ``github:<login>``, which the SSSOM curie map now declares. A
    supplied ORCID (bare or as an orcid.org URL) is normalised to an
    ``orcid:`` CURIE and rejected if malformed. Anything else is ignored — the
    author is the signed-in identity, not a free-text field."""
    supplied = (supplied or "").strip()
    if not supplied or supplied == f"github:{login}":
        return f"github:{login}"
    return sssom_service.orcid_curie(supplied.removeprefix("orcid:"))


@router.post("/api/v2/publish")
async def publish(request: Request, payload: dict = Body(default={})):
    """Commit the current ontology file to GitHub as the signed-in user (PR)."""
    if not config.GH_ENABLED:
        raise ValueError("GitHub integration is not configured")
    u = sessions._user(request)
    if not u:
        return JSONResponse(status_code=401, content={"detail": "Sign in with GitHub first"})
    disease = payload.get("disease") or "ontology"
    message = payload.get("message") or f"Update {disease}"
    comment = (payload.get("comment") or "").strip()

    # Confirmed (positive) + flagged (negative) cross-references from a
    # reference-review session (also written to the mapping files further below).
    confirmed = payload.get("confirmed") or []
    flagged = payload.get("flagged") or []
    # Cells judged to have no term at all in the target database.
    absent = payload.get("absent") or []
    # author_id lands in the published SSSOM. An ORCID is validated here rather
    # than trusted from localStorage: a typo in this column is permanent and
    # unattributable, and an unexpandable CURIE makes the file fail validation.
    author = _mapping_author(payload.get("author"), u["identity"]["login"])
    any_review = bool(confirmed or flagged or absent)

    # A curator may not confirm a mapping id they added themselves; the frontend
    # blocks it, and this is the boundary check behind that.
    own = stores.ID_AUTHORS.authors()
    login = u["identity"]["login"]
    source_branch = workspace._source_branch(request)   # this curator's baseline, not a global

    # A retry of a publish that actually succeeded returns the original result
    # rather than committing the same judgments twice.
    request_id = (payload.get("request_id") or "").strip()[:64]
    already = _remembered_publish(login, request_id)
    if already is not None:
        log.info("Publish %s by @%s already completed; returning the first result",
                 request_id, login)
        return {**already, "repeated": True}
    for c in confirmed:
        for ident in (c.get("ids") or []):
            if own.get(f"{c.get('iri')}|{c.get('db')}|{ident}") == login:
                return JSONResponse(status_code=400, content={
                    "detail": f"@{login} added {c.get('db')} id {ident} — another curator must confirm it"})

    # Optionally fold each confirmed cross-reference's synonyms and direct children
    # into the disease record itself. Off unless the client opts in, so a plain
    # mappings review never rewrites disease fields.
    apply_enrich = bool(payload.get("apply_enrichment")) and bool(confirmed)

    # Record the review in each affected disease's changelog before snapshotting
    # the ontology for the commit. A write copy is used so the entries land in
    # this user's working ontology, mirroring how field edits are handled.
    svc = workspace.service_for(request, write=True) if any_review else workspace.service_for(request)
    enrich_note = ""
    # The changelog entries and the enrichment are applied to the working copy
    # *before* the eight GitHub calls that publish it, so any failure among them
    # used to leave the entries applied: the curator saw "Publish failed",
    # clicked again, and the changelog and enrichment were written a second
    # time. Snapshot first and roll back on failure.
    rollback = svc.path.read_bytes() if any_review else None
    if any_review:
        svc.log_xref_review(confirmed, flagged, editor=login, absent=absent)
        if apply_enrich:
            # Per-item selection from the preview drawer; absent means "all",
            # which is what the single all-or-nothing checkbox used to mean.
            got = svc.apply_enrichment(confirmed, editor=login,
                                       selected=payload.get("enrichment_selection"))
            if got["diseases"]:
                enrich_note = (f"## Enrichment\n\nFolded confirmed cross-references into "
                               f"{got['diseases']} disease(s): +{got['synonyms_added']} "
                               f"synonym(s), +{got['subtypes_added']} clinical subtype(s).")
        workspace._mark_dirty(request)
        workspace._touch(request, *(c.get("iri") for c in confirmed + flagged + absent))
    content = svc.path.read_bytes()

    # Diff current vs the source branch to summarise previous -> new values.
    import os as _os
    import tempfile
    summary = ""
    tmp_path = None
    try:
        data = await gh.get_file_at(u["token"], config.GH_OWNER, config.GH_REPO,
                                    config.GH_ONTOLOGY_PATH, source_branch)
        tf = tempfile.NamedTemporaryFile(suffix=".owl", delete=False)
        tf.write(data); tf.close(); tmp_path = tf.name
        baseline = OntologyService(tmp_path)
        summary = diff_service.build_change_summary(svc, baseline,
                                                    touched_iris=workspace.USER_TOUCHED.get(login))
    except Exception as e:
        log.warning("Could not build change summary against source branch %s: %s", source_branch, e)
        summary = "_Change summary unavailable (could not load the source-branch baseline)._"
    finally:
        if tmp_path:
            try:
                _os.unlink(tmp_path)
            except OSError as e:
                log.debug("Could not remove temp baseline %s: %s", tmp_path, e)

    # Confirmed / flagged cross-references also accumulate into the
    # SSSOM + equivalencies mapping files.
    reuse_branch = payload.get("branch") or None
    labels = payload.get("labels") or ["edit term"]
    extra_files = {}
    SS_PATH = config.MAPPINGS_SSSOM_PATH
    EQ_PATH = config.MAPPINGS_EQUIV_PATH
    map_note = ""
    if any_review:
        async def _read(path):
            try:
                return (await gh.get_file_at(u["token"], config.GH_OWNER, config.GH_REPO,
                                             path, source_branch)).decode("utf-8")
            except Exception as e:
                log.debug("Could not read existing mapping file %s@%s, starting fresh: %s", path, source_branch, e)
                return ""
        files = sssom_service.build(confirmed, author, await _read(SS_PATH), await _read(EQ_PATH),
                                    flagged=flagged, absent=absent)
        extra_files = {SS_PATH: files["sssom"].encode("utf-8"),
                       EQ_PATH: files["equiv"].encode("utf-8")}
        # This used to read "3 new 5 positive / 2 negative / 1 no-term-found
        # judgment(s) added to …" — the new-row count and the verdict counts
        # concatenated with no connecting words, in every review PR.
        verdicts = ", ".join(
            f"{n} {label}" for n, label in
            ((len(confirmed), "confirmed"), (len(flagged), "flagged"),
             (len(absent), "no term in database")) if n)
        map_note = (f"## Reviewed mappings\n\n"
                    f"{verdicts or 'No'} — {files['added']} new row(s) "
                    f"added to `{SS_PATH}` (SSSOM) and `{EQ_PATH}`.")

    parts = []
    if comment:
        # Quoted, not interpolated: the comment is authenticated input going
        # into a public artefact, and markdown or raw HTML in it would render.
        quoted = "\n".join("> " + line for line in comment.splitlines())
        parts.append("**Curator comment:**\n\n" + quoted)
    parts.append(f"Submitted via the ARI Metadata Manager by @{u['identity']['login']}.")
    if map_note:
        parts.append(map_note)
    if enrich_note:
        parts.append(enrich_note)
    parts.append("## Changes\n\n" + summary)
    pr_body = "\n\n".join(parts)

    try:
        result = await gh.publish_file(
            token=u["token"], owner=config.GH_OWNER, repo=config.GH_REPO,
            base_branch=workspace._pr_base(request),
            path=config.GH_ONTOLOGY_PATH, content_bytes=content, disease_name=disease,
            message=message, identity=u["identity"], pr_body=pr_body, extra_files=extra_files,
            reuse_branch=reuse_branch,
            labels=(labels + ["sssom"] if (any_review and "sssom" not in labels) else labels))
    except Exception:
        if rollback is not None:
            workspace._restore_working_copy(login, svc, rollback)
        raise

    _remember_publish(login, request_id, result)
    workspace._clear_touched(login)   # published; later PRs must not re-describe this work
    return result
