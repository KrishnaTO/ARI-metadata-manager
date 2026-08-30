// The record view: selecting a disease, rendering the record header, the
// reading column (definition, citations, the disease-story spine) and the
// sidebar (synonyms, target tissue, the cross-reference ledger, subtypes).

// The deep-dive container lives in the shell but is re-parented into the reading
// column on every render, so a category opens as an inline expansion under the
// story spine instead of as a third column. Held by reference because
// #detail-pane's innerHTML is replaced wholesale on each render.
const DEEP_DIVE = document.getElementById('right-col');
function mountDeepDive(){
  ($('#deep-dive-slot') || $('#layout')).appendChild(DEEP_DIVE);
}

// The start state: nothing selected, just the index rail and a way in. Reached
// on first load, from the wordmark, and from a Back navigation past the first
// disease.
//
// It used to read "Select a disease from the list to view its record" — which on
// a small laptop, where the index is an overlay and the rail can be unpinned, was
// pointing at a list that was not on screen and offering nothing to click
// (issue #94). Both routes in are buttons now.
function showStartPage(){
  state.activeIri = null;
  state.detail = null;
  setMode('read');
  closeRightPanel();
  $('#tree-pane').querySelectorAll('.selected').forEach(el => el.classList.remove('selected'));
  $('#detail-pane').innerHTML = `<div class="empty-state start-state">
    <h2>No disease open</h2>
    <p>Open one from the index, or search by name, synonym or code.</p>
    <div class="start-actions">
      <button class="btn primary" id="start-index">Open the disease index</button>
      <button class="btn" id="start-search">Search diseases</button>
    </div>
  </div>`;
  $('#start-index')?.addEventListener('click', () => showDiseaseIndex());
  $('#start-search')?.addEventListener('click', () => $('#search')?.focus());
  mountDeepDive();
  syncCurateAccess();
}

// opts.history === false  -> selection driven by the URL (initial load or a
//   Back/Forward navigation); don't push another history entry.
// opts.scroll === true     -> reveal and scroll the tree to the row (used when
//   the selection came from a link rather than a visible click).
async function selectDisease(iri, opts = {}){
  state.activeIri = iri;
  // Cleared first so leaving curate mode doesn't repaint the outgoing record.
  state.detail = null;
  setMode('read');
  closeRightPanel();
  $('#tree-pane').querySelectorAll('.selected').forEach(el => el.classList.remove('selected'));
  $('#tree-pane').querySelectorAll(`[data-iri="${CSS.escape(iri)}"]`).forEach(el => el.classList.add('selected'));
  // Mirror the selection in the URL so it can be shared / bookmarked.
  if (opts.history !== false) pushDiseaseHash(iri);
  if (opts.scroll) scrollTreeToActive();
  const d = await api(`/api/v2/disease/${encodeURIComponent(iri)}`);
  state.detail = d;
  syncCurateAccess();
  renderDetail(d);
}

// ------------------------------------------------------------------ header
// Evidence state closes the breadcrumb. This ontology's `evidence_quality` holds
// free text — a strength ("High", "Unconfirmed") for some records and a mechanism
// ("Antibody", "TCELL") for others — so the value is shown verbatim and only
// coloured when it actually reads as a strength.
function evidenceHTML(d){
  const raw = first(d.evidence_quality);
  if (!raw) return '';
  const l = String(raw).toLowerCase();
  let cls = '';
  if (/\b(established|confirmed|high|strong)\b/.test(l)) cls = ' established';
  else if (/\b(probable|suspected|unconfirmed|moderate|likely)\b/.test(l)) cls = ' probable';
  return `<span class="crumb-sep">/</span><span class="evidence${cls}" title="Evidence quality">${esc(raw)}</span>`;
}

function recordHeadHTML(d){
  let crumbs = '';
  if (d.ari_id?.length){
    // Stored ARI IDs carry an "ARI:" (or "ARI_") prefix; normalise for display.
    // The full IRI is break-all noise in the breadcrumb at this size, so it is
    // the copy button's payload and tooltip rather than visible text.
    const bareId = String(d.ari_id[0]).replace(/^ARI[\s:_-]*/i, '');
    crumbs += `<span class="ari-id">ARI:${esc(bareId)}` +
      `<button class="copy-btn" data-copy="${esc(d.iri)}" title="Copy IRI — ${esc(d.iri)}" ` +
      `aria-label="Copy the IRI for this disease">${COPY_ICON}</button></span>`;
  }
  if (d.is_grouping){
    crumbs += `${crumbs ? '<span class="crumb-sep">/</span>' : ''}<span>Umbrella category</span>`;
  }
  if (d.parent_disease?.length){
    crumbs += `${crumbs ? '<span class="crumb-sep">/</span>' : ''}<span>Subtype of ` +
      `<a href="#" class="parent-link" data-iri="${esc(d.parent_disease[0].iri)}">${esc(d.parent_disease[0].name)}</a></span>`;
  }
  crumbs += evidenceHTML(d);

  return `<div class="rec-head">
    <div class="rec-head-main">
      <div class="crumbs">${crumbs}</div>
      <h1>${esc(d.name)}${d.obsolete ? ' <span class="obsolete-tag">(obsolete)</span>' : ''}</h1>
    </div>
    <div class="rec-actions">
      <button class="btn copy-btn" data-copy="${esc(diseaseLinkUrl(d.iri))}" title="Copy a shareable link to this disease">Copy link</button>
      <button class="btn primary" id="edit-toggle" ${canCurate() ? '' : 'disabled'} title="${esc(curateHint())}">${state.editMode ? 'Done editing' : 'Edit record'}</button>
    </div>
  </div>`;
}

// ------------------------------------------------------- citations + byline
// A citation's display text: its stored label, else "PMID: n" for PubMed, else
// the host it points at.
function citeLabel(c){
  if (c.text) return esc(c.text);
  const m = String(c.url).match(/pubmed\.ncbi\.nlm\.nih\.gov\/(\d+)/);
  if (m) return `PMID: ${m[1]}`;
  try { return esc(new URL(c.url).hostname.replace(/^www\./, '')); }
  catch (e){ return 'Source'; }
}

function citationsHTML(d){
  const shownUrls = new Set();
  const cites = [];
  for (const s of (d.def_source || [])){
    for (const c of parseDefSrc(String(s))){
      if (c.url) shownUrls.add(c.url);
      cites.push(c);
    }
  }
  for (const p of (d.pubmed || [])){
    const u = String(p || '').trim();
    if (u && !shownUrls.has(u)) cites.push({ text: '', url: u });
  }

  let byline = '';
  if (d.authors?.length){
    const [who, link] = String(d.authors[0]).split(' | ');
    const date = d.author_date?.length ? `, ${esc(d.author_date[0])}` : '';
    const whoHtml = link ? `<a href="${esc(link)}" target="_blank" rel="noopener">${esc(who)}</a>` : esc(who);
    byline = `<span class="byline">Profile by ${whoHtml}${date}</span>`;
  }
  if (!cites.length && !byline) return '';

  let html = '<div class="def-sources">';
  for (const c of cites){
    // Only a real "PMID: n" citation gets the mono treatment; a PubMed search
    // URL is just another link.
    const isPmid = /pubmed\.ncbi\.nlm\.nih\.gov\/\d+/.test(String(c.url));
    if (c.url){
      html += `<span class="def-source-item${isPmid ? ' pmid' : ''}">` +
        `<a href="${esc(c.url)}" target="_blank" rel="noopener">${citeLabel(c)}</a></span>`;
    } else if (c.text){
      html += `<span class="def-source-item"><span class="src-label">Source</span>${esc(c.text)}</span>`;
    }
  }
  return html + byline + '</div>';
}

// The opening sentence of a definition, as plain text — the pinned context line
// has one line to work with, so markdown and the rest of the paragraph go.
function firstSentence(text){
  const flat = String(text).replace(/[*_`#>]/g, '').replace(/\s+/g, ' ').trim();
  const end = flat.search(/\.\s|\.$/);
  return end > 0 ? flat.slice(0, end + 1) : flat;
}

// ---------------------------------------------------------- story spine
// One column per numbered STORY_GROUPS entry; the category lines inside it are
// the click targets into the deep dive (keyed by data-box).
function boxNote(b){
  if (b.count > 0) return String(b.count);
  if (state.editMode && state.schema[b.key]) return '+ add';
  return b.note || '';
}
function boxHTML(b){
  const active = state.activeBox === b.key ? ' active' : '';
  // The story-spine categories were plain divs: querying the accessibility tree
  // for "Symptoms" returned nothing while the link was plainly on screen. They
  // are buttons now, so they are reachable, announced, and Enter/Space works.
  return `<button type="button" class="box${active}" data-box="${b.key}"` +
    ` aria-pressed="${active ? 'true' : 'false'}"><span class="lbl">${esc(b.label)}</span>` +
    `<span class="count">${esc(boxNote(b))}</span></button>`;
}

function visibleKeys(grp, boxByKey, isGrouping){
  let keys = grp.keys.filter(k => {
    const b = boxByKey[k];
    return b && (b.show || (state.editMode && state.schema[k]));
  });
  if (isGrouping) keys = keys.filter(k => GROUPING_STORY_KEYS.includes(k));
  return keys;
}

function storyHTML(d, boxByKey){
  // A grouping/umbrella category carries no disease-specific clinical metadata,
  // so its story is only the record-keeping step.
  if (d.is_grouping){
    const keys = visibleKeys(STORY_GROUPS[STORY_GROUPS.length - 1], boxByKey, true);
    return `<div class="story record-only"><div class="story-step">` +
      keys.map(k => boxHTML(boxByKey[k])).join('') + `</div></div>`;
  }
  // The spine reserved five equal columns whether or not they had content: for a
  // record with three empty steps that left ~124px each for the two that had
  // something to say, enough to break "Biomarkers & treatments" over three lines
  // (issue #101). In read mode an empty step is not rendered and the populated
  // ones divide the full width. Curate mode keeps all five — the "+ add"
  // affordances in an empty step are the whole point there.
  const steps = [];
  for (const grp of STORY_GROUPS){
    if (!grp.num) continue;                       // the Record step lives in the sidebar
    const keys = visibleKeys(grp, boxByKey, false);
    if (!keys.length && !state.editMode) continue;
    const active = keys.includes(state.activeBox) ? ' active' : '';
    steps.push(`<div class="story-step${active}" title="${esc(grp.hint)}">` +
      `<div class="story-head"><span class="story-num">${String(grp.num).padStart(2, '0')}</span>` +
      `<span class="story-title">${esc(grp.title)}</span></div>` +
      keys.map(k => boxHTML(boxByKey[k])).join('') + `</div>`);
  }
  if (!steps.length) return '';
  return `<div class="story" style="--story-n:${steps.length}">${steps.join('')}</div>`;
}

// ------------------------------------------------------------------ sidebar
// The cross-reference ledger. Every database gets a row, in the order the
// reference-review page lists its columns (XREF_MAIN, from the server registry);
// a database with no id renders an em dash rather than disappearing, so the
// ledger doubles as a coverage check. Ids stack one per line, right-aligned —
// several 8-digit SNOMED codes on one line wrap, and a wrapped run reads as
// left-indented against its single-id neighbours.
function xrefLedgerRowsHTML(d){
  let rows = '', filled = 0;
  for (const db of XREF_MAIN){
    const vals = d[db.key] || [];
    if (vals.length){
      filled++;
      const ids = vals.map(v =>
        `<a class="xr-id" href="${esc(xrefLink(db.key, v))}" target="_blank" rel="noopener">${esc(v)}</a>`).join('');
      rows += `<div class="xr"><span class="xr-db">${esc(db.label)}</span><span class="xr-ids">${ids}</span></div>`;
    } else {
      rows += `<div class="xr empty"><span class="xr-db">${esc(db.label)}</span><span class="xr-ids"><span class="xr-id">&mdash;</span></span></div>`;
    }
  }
  return { rows, filled, total: XREF_MAIN.length };
}

// A sidebar block: a section label that doubles as its collapse toggle, over
// the block's body. Blocks open expanded; see toggleSideBlock in core.js.
function sideBlockHTML(key, labelHTML, bodyHTML){
  const open = !SIDE_COLLAPSED.has(key);
  return `<div class="side-block${open ? '' : ' collapsed'}" data-block="${esc(key)}">` +
    `<button class="section-label block-toggle" data-block-toggle="${esc(key)}" aria-expanded="${open}">` +
    `${CARET_ICON}<span class="block-label">${labelHTML}</span></button>` +
    `<div class="block-body">${bodyHTML}</div></div>`;
}

function xrefLedgerHTML(d){
  const { rows, filled, total } = xrefLedgerRowsHTML(d);
  return sideBlockHTML('xrefs',
    `Cross-references<span class="count">${filled}/${total}</span>`,
    `<div class="xref-ledger">${rows}</div>`);
}

function sidebarHTML(d, boxByKey){
  let html = '<aside class="rec-side">';

  if (d.synonyms?.length){
    html += sideBlockHTML('synonyms', 'Synonyms',
      `<div class="chips synonyms">${d.synonyms.map(s => `<span>${esc(s)}</span>`).join('')}</div>`);
  }
  if (d.tissue_targets?.length){
    html += sideBlockHTML('tissue', 'Target tissue',
      `<div class="chips">${d.tissue_targets.map(t => `<span class="tissue-chip">${esc(t.name)}</span>`).join('')}</div>`);
  }

  html += xrefLedgerHTML(d);

  // Clinical subtypes / variants (from the report Subtypes sheet): "name — description".
  // Each may link to an existing disease; unlinked ones can be promoted into a
  // new child disease while editing.
  const subs = d.clinical_subtypes_parsed || [];
  if (subs.length){
    let items = '';
    for (const sub of subs){
      let linkHtml = '';
      if (sub.link_iri && sub.link_name){
        linkHtml = ` <a href="#" class="parent-link subtype-link" data-iri="${esc(sub.link_iri)}">&rarr; ${esc(sub.link_name)}${sub.link_obsolete ? ' (obsolete)' : ''}</a>`;
      } else if (sub.link_iri){
        linkHtml = ` <span class="subtype-broken" title="Linked disease not found in this ontology">broken link</span>`;
      }
      const btn = (state.editMode && !sub.link_iri)
        ? ` <button class="hbtn subtype-new-btn" data-subtype-name="${esc(sub.name)}" title="Create this subtype as a new disease (child of this disease)">New disease</button>`
        : '';
      items += `<li><strong>${esc(sub.name)}</strong>${sub.description ? ' &mdash; <span class="sub-desc">' + esc(sub.description) + '</span>' : ''}${linkHtml}${btn}</li>`;
    }
    html += sideBlockHTML('subtypes', 'Clinical subtypes', `<ul class="subtype-list">${items}</ul>`);
  }

  // External reference links (Cleveland Clinic, Mayo, registries, ...)
  if (d.ref_links?.length){
    let links = '';
    for (const ref of d.ref_links){
      const idx = String(ref).lastIndexOf(' | ');
      const text = idx >= 0 ? ref.slice(0, idx) : ref;
      const url = idx >= 0 ? ref.slice(idx + 3) : ref;
      links += `<a class="ref-link" href="${esc(url)}" target="_blank" rel="noopener">${esc(text)}${EXT_ICON}</a>`;
    }
    html += sideBlockHTML('refs', 'External references', `<div class="ref-links">${links}</div>`);
  }

  // The unnumbered "Record" story step (changelog + feedback) closes the sidebar
  // as two text links rather than boxes.
  const recordGroup = STORY_GROUPS[STORY_GROUPS.length - 1];
  const recordKeys = visibleKeys(recordGroup, boxByKey, false);
  if (recordKeys.length && !d.is_grouping){
    html += sideBlockHTML('record', 'Record',
      `<div class="record-links">${recordKeys.map(k => boxHTML(boxByKey[k])).join('')}</div>`);
  }

  return html + '</aside>';
}

// ------------------------------------------------------------------ render
function renderDetail(d){
  const boxByKey = {};
  for (const b of boxDefs(d)) boxByKey[b.key] = b;

  let html = `<div class="detail${d.obsolete ? ' obsolete' : ''}">`;
  html += recordHeadHTML(d);
  // Populated asynchronously with any open PRs whose branch targets this disease.
  html += `<div id="disease-pr-banner"></div>`;
  if (state.editMode){
    html += `<div class="edit-banner"><strong>Curate mode</strong> &mdash; the disease fields are open below; pick a category in the story to add / edit / delete its data items.</div>`;
  }

  html += `<div class="rec-body"><div class="rec-read">`;
  // Opening a category scrolls the definition off the top and puts the deep-dive
  // card where it was — so the thing you are comparing the detail against is
  // exactly what disappears. This condensed line sticks to the top of the reading
  // column while a deep dive is open, and is hidden the rest of the time
  // (issue #101).
  if (d.definition){
    html += `<div class="rec-context" aria-hidden="true"><span class="rc-name">${esc(d.name)}</span>` +
      `<span class="rc-def">${esc(firstSentence(d.definition))}</span></div>`;
  }
  if (d.definition) html += `<div class="definition">${mdToHtml(d.definition)}</div>`;
  html += citationsHTML(d);
  if (d.is_grouping){
    html += `<div class="grouping-note" style="margin-top:24px"><strong>Grouping / umbrella category.</strong> Clinical disease metadata (symptoms, antibodies, genetics, treatments, …) isn't tracked here — a grouping is defined by its definition, cross-references, clinical subtypes and member diseases.</div>`;
  }
  html += `<div class="section-label">${d.is_grouping ? 'Record' : 'Disease story'}</div>`;
  html += storyHTML(d, boxByKey);
  html += `<div id="deep-dive-slot"></div>`;
  html += `</div>`;
  html += sidebarHTML(d, boxByKey);
  html += `</div></div>`;

  $('#detail-pane').innerHTML = html;
  mountDeepDive();

  loadDiseasePRs(d);

  $('#detail-pane').querySelectorAll('.parent-link').forEach(a =>
    a.addEventListener('click', ev => { ev.preventDefault(); selectDisease(a.dataset.iri); }));

  $('#detail-pane').querySelectorAll('.copy-btn').forEach(b =>
    b.addEventListener('click', () => copyToClipboard(b.dataset.copy || '')));

  $('#detail-pane').querySelectorAll('[data-block-toggle]').forEach(btn =>
    btn.addEventListener('click', () => {
      const block = btn.closest('.side-block');
      const open = block.classList.toggle('collapsed') === false;
      btn.setAttribute('aria-expanded', String(open));
      toggleSideBlock(btn.dataset.blockToggle);
    }));

  $('#detail-pane').querySelectorAll('.subtype-new-btn').forEach(btn =>
    btn.addEventListener('click', () =>
      openNewDiseaseModal({ label: btn.dataset.subtypeName, parent_iri: d.iri })));

  // The edit control lives inside the record, which is re-rendered on every
  // change, so it is wired here rather than once at load. Leaving edit mode
  // closes the field form, so it goes through the same discard check as Cancel.
  $('#edit-toggle')?.addEventListener('click', () =>
    state.editMode ? cancelFieldEdits() : setMode('curate'));

  $('#detail-pane').querySelectorAll('.box').forEach(box => {
    box.addEventListener('click', () => {
      const key = box.dataset.box;
      // Opening a category replaces the disease-field form, so unsaved work in
      // it must be confirmed away first.
      if (!confirmDiscardEdits()) return;
      if (state.activeBox === key){ closeRightPanel(); return; }
      state.activeBox = key;
      $('#detail-pane').querySelectorAll('.box').forEach(b => b.classList.remove('active'));
      $('#detail-pane').querySelectorAll('.story-step').forEach(s => s.classList.remove('active'));
      box.classList.add('active');
      box.closest('.story-step')?.classList.add('active');
      openBoxDetail(d, key);
    });
  });
}

function boxDefs(d){
  return [
    { key:'prevalence', label:'Prevalence', count: 0, note:'data', show: (d.prevalence_per_100k?.length || d.prevalence_desc?.length) },
    { key:'symptoms', label:'Symptoms', count: d.symptoms?.length||0, show: d.symptoms?.length },
    { key:'environmental', label:'Environmental', count: d.environmental_factors?.length||0, show: d.environmental_factors?.length },
    { key:'antibodies', label:'Antibodies', count: d.antibodies?.length||0, show: d.antibodies?.length },
    { key:'treatments', label:'Treatments', count: d.treatments?.length||0, show: d.treatments?.length },
    { key:'etiology', label:'Etiology', count: d.etiology?.length||0, show: d.etiology?.length },
    { key:'genetic', label:'Genetics', count: d.genetic?.length||0, show: d.genetic?.length },
    { key:'biomarkers', label:'Biomarkers', count: d.biomarkers?.length||0, show: d.biomarkers?.length },
    { key:'pathophysiology', label:'Pathophysiology', count: d.pathway?.length||0, show: d.pathway?.length },
    { key:'cytokines', label:'Cytokines', count: d.cytokines?.length||0, show: d.cytokines?.length },
    { key:'tcells', label:'T-cells', count: d.tcells?.length||0, show: d.tcells?.length },
    { key:'apcs', label:'APCs', count: d.apcs?.length||0, show: d.apcs?.length },
    { key:'transcription', label:'Transcription factors', count: d.transcription_factors?.length||0, show: d.transcription_factors?.length },
    { key:'innate', label:'Innate immunity', count: d.innate_components?.length||0, show: d.innate_components?.length },
    { key:'complement', label:'Complement', count: d.complement?.length||0, show: d.complement?.length },
    { key:'receptors', label:'Receptors', count: d.receptors?.length||0, show: d.receptors?.length },
    { key:'netosis', label:'NETosis', count: d.netosis?.length||0, show: d.netosis?.length },
    { key:'inflammasome', label:'Inflammasome', count: d.inflammasome?.length||0, show: d.inflammasome?.length },
    { key:'apr', label:'Acute phase reactants', count: d.acute_phase_reactants?.length||0, show: d.acute_phase_reactants?.length },
    { key:'antigens', label:'Antigens', count: d.antigens?.length||0, show: d.antigens?.length },
    { key:'changelog', label:'Change log', count: d.changelog?.length||0, show: true, note:'history' },
    { key:'feedback', label:'Feedback', count: 0, show: true, note:'comment' },
  ];
}

// ----------------------------------------------------------------- OPEN-PR CHANGES (issue #19)
// Mirror github_service.slugify: lowercase, non-alphanumerics -> "-", trimmed, capped.
function ariSlug(name){
  return (String(name || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60)) || 'disease';
}

// Open PRs are fetched once per page load and reused across disease selections.
let _openPRsCache = null;
async function getOpenPRs(){
  if (_openPRsCache) return _openPRsCache;
  try { const r = await api('/api/v2/open-prs'); _openPRsCache = (r && r.prs) || []; }
  catch(e){ _openPRsCache = []; }
  return _openPRsCache;
}
// A publish branch is `edit/<login>/<slug>-<timestamp>`; the disease slug is the
// last path segment with the trailing `-<timestamp>` removed.
function prTargetsDisease(pr, slug){
  const seg = String(pr.branch || '').split('/').pop().replace(/-\d+$/, '');
  return seg === slug;
}
async function loadDiseasePRs(d){
  const el = $('#disease-pr-banner');
  if (!el) return;
  const prs = await getOpenPRs();
  if (!prs.length) return;
  // Guard against a stale async result after the user switched diseases.
  if (state.detail !== d) return;
  const slug = ariSlug(d.name);
  const matches = prs.filter(pr => prTargetsDisease(pr, slug));
  if (!matches.length) return;
  el.innerHTML = `<div class="pr-banner"><div class="pr-banner-head">${matches.length} open pull request${matches.length===1?'':'s'} with unreviewed changes to this disease</div>` +
    matches.map(pr => `<a class="pr-banner-item" href="${esc(pr.url)}" target="_blank" rel="noopener">` +
      `<span class="pr-num">#${esc(pr.number)}</span> ${esc(pr.title)} <span class="pr-author">@${esc(pr.author)}</span>${EXT_ICON}</a>`).join('') +
    `</div>`;
}
