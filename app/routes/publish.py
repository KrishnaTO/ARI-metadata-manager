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

from .. import atomic_store, config, diff_service, merge_service, sessions, sssom_service, stores, workspace
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


async def _baseline_service(request, u):
    """The source branch's ontology, as a service. Raises if it cannot be read.

    The pending-changes list and the pull-request summary compare against this,
    and the publish itself is built on top of it. The caller is responsible for
    deleting ``service.path``.
    """
    import tempfile
    data = await gh.get_file_at(u["token"], config.GH_OWNER, config.GH_REPO,
                                config.GH_ONTOLOGY_PATH, workspace._source_branch(request))
    tf = tempfile.NamedTemporaryFile(suffix=".owl", delete=False)
    tf.write(data)
    tf.close()
    return OntologyService(tf.name)


@router.get("/api/v2/pending-changes")
async def pending_changes(request: Request):
    """The diseases this curator has changed, and what changed in each.

    The publish dialog offered a free-text title box and nothing else, so a
    curator opening a pull request could not see which diseases it carried, and
    the default title named whichever record happened to be on screen (issue
    #25). ``title`` is generated from the list.

    Empty for an anonymous user, and empty — rather than an error — when the
    source branch cannot be read, so the dialog degrades to what it did before
    instead of refusing to open.
    """
    u = sessions._user(request) if config.GH_ENABLED else None
    login = sessions._login(request)
    if not u or not login:
        return {"changes": [], "title": "Update ontology", "source_branch": ""}
    touched = workspace.touched(login)
    baseline = None
    try:
        baseline = await _baseline_service(request, u)
        changes = diff_service.list_changes(workspace.service_for(request), baseline, touched)
    except Exception as e:
        log.warning("Could not list pending changes against the source branch: %s", e)
        return {"changes": [], "title": "Update ontology",
                "source_branch": workspace._source_branch(request),
                "unavailable": True}
    finally:
        if baseline is not None:
            _discard(baseline.path)
    return {"changes": changes, "title": diff_service.title_for(changes),
            "source_branch": workspace._source_branch(request)}


def _discard(path):
    import os as _os
    try:
        _os.unlink(path)
    except OSError as e:
        log.debug("Could not remove temp baseline %s: %s", path, e)


@router.post("/api/v2/discard")
async def discard_diseases(request: Request, payload: dict = Body(...)):
    """Take the source branch's version of the named diseases.

    The way out of a publish conflict. Fetching the branch again is the blunt
    version of this: it drops the whole working copy, so one collision cost the
    curator every unpublished edit and verdict they were holding, on diseases
    that had nothing to do with it. This replaces only the named diseases with
    the branch's version of them, forgets that this curator touched them, and
    drops their verdicts. Everything else survives, and the publish that was
    refused can be retried straight away.
    """
    if not config.GH_ENABLED:
        raise ValueError("GitHub integration is not configured")
    u = sessions._user(request)
    if not u:
        return JSONResponse(status_code=401, content={"detail": "Sign in with GitHub first"})
    iris = sorted({i for i in (payload.get("iris") or []) if i})
    if not iris:
        return JSONResponse(status_code=400, content={
            "detail": "Name at least one disease to take the source branch's version of."})
    login = u["identity"]["login"]
    if not (config.USER_DIR / f"{login}.owl").exists():
        return JSONResponse(status_code=400, content={
            "detail": "You have no working copy, so there is nothing to discard."})

    try:
        baseline = await _baseline_service(request, u)
    except Exception as e:
        log.error("Could not read %s at %s to take its version of %d disease(s): %s",
                  config.GH_ONTOLOGY_PATH, workspace._source_branch(request), len(iris), e)
        return JSONResponse(status_code=502, content={
            "detail": "Could not read the ontology on the source branch. "
                      "Nothing was discarded — try again."})

    svc = workspace.user_service(login)
    # The working copy is snapshotted first: the graft can refuse part-way
    # through (see merge_service), and half a disease taken from the branch is
    # worse than the collision this is resolving.
    snapshot = svc.path.read_bytes()
    try:
        merge_service.graft_diseases(baseline, svc, iris)
        svc._save()
    except Exception:
        workspace._restore_working_copy(login, svc, snapshot)
        raise
    finally:
        _discard(baseline.path)
    # owlready2 caches an entity's values on the Python object and the graft
    # writes triples underneath that cache, so the saved file is right while the
    # loaded copy would still read back the discarded edit. Drop it; the next
    # read adopts the file just written.
    workspace.USER_SVC.pop(login, None)
    workspace.forget(login, *iris)
    log.info("@%s took %s's version of %d disease(s)", login,
             workspace._source_branch(request), len(iris))
    return {"ok": True, "discarded": len(iris)}


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

    # The diseases this publish is allowed to speak for: the ones edited since
    # the last publish, plus the ones reviewed in this request. Everything else
    # in the commit comes from the source branch, so an untouched record cannot
    # be reverted to whatever it looked like when the working copy was made.
    review_iris = {c.get("iri") for c in confirmed + flagged + absent if c.get("iri")}
    scope = workspace.touched(login) | review_iris
    if not scope:
        return JSONResponse(status_code=400, content={
            "detail": "Nothing to publish — no disease has been edited or reviewed in this session."})

    # The source branch as it stands now. This is the base of the commit, not
    # just something to diff against: publishing committed the working copy
    # wholesale, so every disease merged into the branch since that copy was
    # taken was reverted by the next save (issue #146).
    try:
        baseline = await _baseline_service(request, u)
    except Exception as e:
        log.error("Could not read %s at %s to rebase this publish onto: %s",
                  config.GH_ONTOLOGY_PATH, source_branch, e)
        return JSONResponse(status_code=502, content={
            "detail": f"Could not read the ontology on {source_branch} to publish against. "
                      "Nothing was committed — try again."})

    rollback = None
    try:
        collisions = merge_service.upstream_edits(svc, baseline, scope)
        if collisions:
            return JSONResponse(status_code=409, content={
                "detail": "These diseases changed on " + source_branch +
                          " after your working copy was made: " +
                          ", ".join(c["name"] for c in collisions) +
                          ". Publishing now would revert them — take " + source_branch +
                          "'s version of those diseases, which drops your work on them "
                          "and keeps the rest, then publish again.",
                "conflicts": collisions})

        enrich_note = ""
        # The changelog entries and the enrichment are applied to the working copy
        # *before* the eight GitHub calls that publish it, so any failure among them
        # used to leave the entries applied: the curator saw "Publish failed",
        # clicked again, and the changelog and enrichment were written a second
        # time. Snapshot first and roll back on failure.
        rollback = svc.path.read_bytes() if any_review else None
        stored_ids = 0
        if any_review:
            svc.log_xref_review(confirmed, flagged, editor=login, absent=absent)
            # A confirmation that only wrote a mapping row left the disease
            # itself unchanged, so confirming a MONDO or Orphanet term the ARI
            # import never carried changed nothing anyone could see (issue #146).
            stored_ids = svc.store_confirmed_xrefs(confirmed, editor=login)
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
            workspace._touch(request, *review_iris)

        # Summarise before the graft, while the baseline is still the branch's.
        summary = diff_service.build_change_summary(svc, baseline, touched_iris=scope)

        # The commit is the source branch with this curator's diseases written
        # over it — the same shape `sssom_service.build` already uses for the
        # mapping files, which is why not one mapping row was lost.
        merge_service.graft_diseases(svc, baseline, scope)
        baseline._save()
        content = baseline.path.read_bytes()
    except Exception:
        # The graft can refuse (see merge_service), and that lands between the
        # changelog entries going in and anything being committed.
        if rollback is not None:
            workspace._restore_working_copy(login, svc, rollback)
        raise
    finally:
        _discard(baseline.path)

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
        if stored_ids:
            map_note += (f" {stored_ids} confirmed id(s) also stored on the disease "
                         f"record itself.")

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
