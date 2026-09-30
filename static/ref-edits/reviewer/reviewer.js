// Mapping-PR reviewer page (/ref-edits/reviewer/). Served as a file, not inline: the
// app's Content-Security-Policy allows only same-origin scripts.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const hintClass = (h) => h.startsWith("Supported") ? "Supported" : h === "Review" ? "Review"
  : h === "Check rejection" ? "Check-rejection" : ["Weak evidence", "Malformed row"].includes(h) ? "Weak" : "plain";

let rows = [], hintFilter = "", sortKey = "line", sortDir = 1, open = new Set(), marks = new Set();
let collapsed = new Set();   // ARI ids whose group is folded
let notes = {};              // row key -> {text, by, at, posted?: {at, url}}
let me = "";                 // the signed-in reviewer's login
const api = (p) => new URL(`../../api/v2/pr-review/${p}`, location.href).href;

const sortValue = {
  line: (r) => (r.status === "removed" ? 1e9 : 0) + r.line,
  marked: (r) => marks.has(key(r)) ? 0 : 1,
  note: (r) => notes[key(r)] ? 0 : 1,
  name: (r) => -r.name_match.points * 10 - r.name_match.overlap,
  def: (r) => -(r.definition_overlap?.score ?? -1),
  support: (r) => -r.evidence.support.length,
};

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(await failure(res));
  return res.json();
}

async function failure(res) {
  let detail = await res.text();
  try { detail = JSON.parse(detail).detail ?? detail; } catch { /* not JSON */ }
  if (res.status === 401) return `${detail}: sign in on the editor first (← above), then reload.`;
  return `${res.status}: ${detail}`;
}

function sendJSON(path, body) {
  return fetch(api(path), {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)});
}

function applyState(state) {
  marks = new Set(Object.keys(state.marks));
  notes = state.notes;
  const mine = Object.values(notes).filter((n) => n.by === me && !n.posted).length;
  $("post-review").textContent = `Post my notes to PR (${mine})`;
  $("post-review").disabled = !mine;
}

async function loadPRs() {
  const prs = await getJSON(api("prs"));
  const want = Number(new URLSearchParams(location.search).get("pr")) || 89;
  $("pr").innerHTML = prs.map((p) =>
    `<option value="${p.number}">#${p.number} · ${esc(p.author)} · ${esc(p.title.slice(0, 70))}</option>`).join("");
  if (!prs.length) { $("body").innerHTML = `<tr><td colspan="12" class="empty">No open PRs change the equivalencies or predictions file.</td></tr>`; return; }
  $("pr").value = prs.some((p) => p.number === want) ? want : prs[0].number;
  loadMatrix();
}

async function loadMatrix() {
  const n = $("pr").value;
  history.replaceState(null, "", `?pr=${n}`);
  $("body").innerHTML = `<tr><td colspan="12" class="empty">Building matrix for #${n}… (fetching PR files, loading ontology)</td></tr>`;
  $("meta").textContent = ""; $("chips").innerHTML = "";
  try {
    const data = await getJSON(api(`prs/${n}`));
    me = data.me; rows = data.rows; open = new Set(); hintFilter = ""; collapsed = new Set();
    $("posted").textContent = "";
    applyState(data);
    const p = data.pr;
    const c = counts(rows), judged = c.confirmed + c.rejected;
    $("meta").innerHTML = `<a href="${esc(p.url)}" target="_blank">#${p.number}</a> by <b>${esc(p.author)}</b> ·
      ${new Set(rows.map((r) => r.ari_id)).size} diseases ·
      ${judged ? `${judged} changed equivalency rows (${c.confirmed} confirmed, ${c.rejected} rejected) ·` : ""}
      ${c.predicted ? `${c.predicted} changed top predictions ·` : ""}
      head ${p.head_sha.slice(0, 7)} vs merge base ${p.merge_base.slice(0, 7)}`;
    fillSelect("db", [...new Set(rows.map((r) => r.db))].sort(), "All databases");
    fillSelect("kind", [...new Set(rows.map((r) => r.name_match.label))], "All name matches");
    render();
  } catch (e) {
    $("body").innerHTML = `<tr><td colspan="12" class="empty">${esc(e.message)}</td></tr>`;
  }
}

function fillSelect(id, values, all) {
  $(id).innerHTML = `<option value="">${all}</option>` + values.map((v) => `<option>${esc(v)}</option>`).join("");
}

function filtered() {
  const q = $("q").value.trim().toLowerCase();
  return rows.filter((r) =>
    (!$("db").value || r.db === $("db").value) &&
    (!$("judgment").value || r.judgment === $("judgment").value) &&
    (!$("kind").value || r.name_match.label === $("kind").value) &&
    (!hintFilter || r.hint === hintFilter) &&
    (!$("marked").value || marks.has(key(r)) === ($("marked").value === "yes")) &&
    (!q || [r.line, r.ari_id, r.ari_label, r.target_id, r.target_label, r.db, ...r.ari_synonyms]
      .join(" ").toLowerCase().includes(q)));
}

function renderChips() {
  const counts = {};
  rows.forEach((r) => counts[r.hint] = (counts[r.hint] || 0) + 1);
  $("chips").innerHTML = `<span class="chip Review ${$("marked").value === "yes" ? "on" : ""}" data-marked="1">Marked for review · ${rows.filter((r) => marks.has(key(r))).length}</span>` + Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([h, c]) =>
    `<span class="chip ${hintClass(h)} ${hintFilter === h ? "on" : ""}" data-hint="${esc(h)}">${esc(h)} · ${c}</span>`).join("");
}

function render() {
  renderChips();
  const list = filtered().sort((a, b) => {
    const f = sortValue[sortKey] || ((r) => String(r[sortKey]).toLowerCase());
    const x = f(a), y = f(b);
    return (x < y ? -1 : x > y ? 1 : 0) * sortDir;
  });
  if (!list.length) { $("body").innerHTML = `<tr><td colspan="12" class="empty">No rows match.</td></tr>`; return; }
  // One group per ARI disease, in the order its first row sorts to; rows keep the sort
  // order within their group.
  const groups = new Map();
  list.forEach((r) => (groups.get(r.ari_id) || groups.set(r.ari_id, []).get(r.ari_id)).push(r));
  $("body").innerHTML = [...groups.values()].map((g) => groupHTML(g) + (collapsed.has(g[0].ari_id) ? ""
    : g.map((r) => rowHTML(r) + (open.has(key(r)) ? detailHTML(r) : "")).join(""))).join("");
  $("collapse").textContent = collapsed.size >= groups.size ? "Expand all" : "Collapse all";
}

const counts = (list) => ({confirmed: list.filter((x) => x.judgment === "manual").length,
  rejected: list.filter((x) => x.judgment === "manual-negative").length,
  predicted: list.filter((x) => x.judgment === "predicted").length});

function groupHTML(g) {
  const r = g[0], c = counts(g);
  const hints = {};
  g.forEach((x) => hints[x.hint] = (hints[x.hint] || 0) + 1);
  const marked = g.filter((x) => marks.has(key(x))).length;
  const noted = g.filter((x) => notes[key(x)]).length;
  return `<tr class="group" data-ari="${esc(r.ari_id)}"><td colspan="12">
    <span class="caret">${collapsed.has(r.ari_id) ? "▸" : "▾"}</span>
    <b>${esc(r.ari_label)}</b> <span class="small">${esc(r.ari_id)}</span>
    <span class="count">${g.length} row${g.length === 1 ? "" : "s"} · ${Object.entries(c).filter(([, n]) => n).map(([k, n]) => `${n} ${k}`).join(" · ")}</span>
    ${marked ? `<span class="tag Review">${marked} marked</span>` : ""}
    ${noted ? `<span class="tag plain">${noted} noted</span>` : ""}
    ${Object.entries(hints).map(([h, c]) => `<span class="tag ${hintClass(h)}">${esc(h)} ${c}</span>`).join("")}
  </td></tr>`;
}

// Prediction rows are keyed apart from judgments, which can name the same target.
// The server's row key (predictions keyed apart from judgments naming the same id).
const key = (r) => r.key;

function rowHTML(r) {
  const m = r.name_match, d = r.definition_overlap, ev = r.evidence;
  const judgment = r.judgment === "manual" ? `<span class="tag Supported">confirmed</span>`
    : r.judgment === "predicted" ? `<span class="tag plain">predicted</span>`
    : `<span class="tag Weak">rejected</span>`;
  const status = r.status === "added" ? (r.judgment === "predicted" ? `<div class="small">new prediction</div>` : "")
    : r.judgment === "predicted" && r.previous ? `<div class="small">replaces ${esc(r.previous)}</div>`
    : `<div class="small">${esc(r.status)}${r.previous ? " from " + esc(r.previous) : ""}</div>`;
  const target = `<a href="${esc(r.target_url || "#")}" target="_blank">${esc(r.db)} ${esc(r.target_id)}</a>
    <div>${esc(r.target_label) || '<span class="small">—</span>'}</div>
    ${r.target_previous_label ? `<div class="small" title="Name in the local data/2-databases snapshot">was “${esc(r.target_previous_label)}”</div>` : ""}
    <a class="small" href="${esc(googleURL(r))}" target="_blank"
      title="Google: ${esc(googleQuery(r))}">Google ARI vs target ↗</a>
    ${r.found && !r.direct ? `<div class="small">via ${esc(r.views.map((v) => v.id).join(", "))}</div>` : ""}`;
  const name = m.kind === "no_data" ? `<span class="small">${esc(m.label)}</span>`
    : `<div>${esc(m.label)}${["partial", "weak"].includes(m.kind) ? ` (${Math.round(m.overlap * 100)}%)` : ""}</div>
       <div class="small">“${esc(m.ari)}” ↔ “${esc(m.target)}”</div>`;
  const def = d ? `<span class="bartrack"><span class="bar" style="width:${Math.round(d.score * 50)}px"></span></span>${Math.round(d.score * 100)}%`
    : `<span class="small">${!r.ari_definition ? "no ARI definition"
      : !r.views.some((v) => v.definition) ? "no target definition"
      : "no words to compare"}</span>`;
  const sup = ev.support.length ? ev.support.map((s) =>
    `<span class="tag same" title="${esc(s.origin)} · via ${esc(s.via.join(", "))}">${esc(s.db)} ${esc(s.id)}${s.origin === "this PR" ? "*" : ""}</span>`).join("")
    : `<span class="small">none</span>`;
  const flags = r.flags.map((f) => `<div class="tag conflict">${esc(f)}</div>`).join("");
  const main = r.on_main === "same on main" ? `<span class="tag same">same</span>`
    : r.on_main === "not on main" ? `<span class="small">not yet</span>` : `<span class="tag conflict">${esc(r.on_main)}</span>`;
  const line = `<a href="${esc(r.line_url)}" target="_blank"
    title="Open line ${r.line} of ${esc(r.file)} in the PR's Files changed to comment on it${r.status === "removed" ? " (removed line, merge-base numbering)" : ""}">${r.line}</a>${r.judgment === "predicted" ? '<div class="small">pred</div>' : ""}`;
  const marked = marks.has(key(r));
  return `<tr class="item ${marked ? "marked" : ""}" data-key="${esc(key(r))}">
    <td class="small">${line}</td>
    <td><input type="checkbox" class="mark" ${marked ? "checked" : ""} title="Mark for review"></td>
    <td><b>${esc(r.ari_label)}</b><button class="copy" data-copy="${esc(r.ari_label)}"
      title="Copy the disease name">⧉</button><div class="small">${esc(r.ari_id)}</div></td>
    <td>${target}</td><td>${judgment}${status}</td><td>${name}</td><td>${def}</td>
    <td>${sup}</td><td>${flags}</td><td>${main}</td>
    <td><span class="tag ${hintClass(r.hint)}">${esc(r.hint)}</span><div class="small">score ${r.score}</div></td>
    <td><textarea class="note ${notes[key(r)] ? "has-note" : ""}" rows="2" placeholder="Add a note…"
      title="${notes[key(r)] ? `Saved by @${esc(notes[key(r)].by)} ${esc(notes[key(r)].at)}` : "Saved when you leave the box"}">${esc(notes[key(r)]?.text || "")}</textarea>
      <div class="small note-status">${noteStatus(notes[key(r)])}</div></td></tr>`;
}

function noteStatus(n) {
  if (!n) return "";
  const who = n.by === me ? "" : `@${esc(n.by)} · `;
  return who + (n.posted ? `<a href="${esc(n.posted.url)}" target="_blank">posted ↗</a>` : "not posted");
}

// "ARI name" vs "target label"; without a target label, the id stands in for it.
function googleQuery(r) {
  return r.target_label ? `"${r.ari_label}" vs "${r.target_label}"` : `"${r.ari_label}" ${r.db} ${r.target_id}`;
}
function googleURL(r) {
  return `https://www.google.com/search?q=${encodeURIComponent(googleQuery(r))}`;
}

function highlight(text, words) {
  if (!words?.length) return esc(text);
  const re = new RegExp(`\\b(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})\\b`, "gi");
  return esc(text).replace(re, "<mark>$1</mark>");
}

function detailHTML(r) {
  const shared = r.definition_overlap?.shared;
  const list = (xs) => xs.length ? esc(xs.join(" · ")) : '<span class="small">—</span>';
  const ari = `<div class="card"><h3>ARI · ${esc(r.ari_id)} ${esc(r.ari_label)}</h3><dl>
    <dt>Synonyms</dt><dd>${list(r.ari_synonyms)}</dd>
    <dt>Clinical subtypes</dt><dd>${list(r.ari_subtypes)}</dd>
    <dt>Definition</dt><dd>${r.ari_definition ? highlight(r.ari_definition, shared) : '<span class="small">—</span>'}</dd></dl></div>`;
  const targets = r.views.length ? r.views.map((v) => `<div class="card">
    <h3>${v.direct ? "" : "Hub · "}<a href="${esc(v.url || "#")}" target="_blank">${esc(v.id)}</a> ${esc(v.label)}</h3>
    <div class="small">${esc(v.source)}${v.inactive ? " · inactive concept" : ""}</div><dl>
    ${v.facts.length ? `<dt>Concept</dt><dd>${esc(v.facts.join(" · "))}</dd>` : ""}
    ${v.previous_label ? `<dt>Local index name</dt><dd>${esc(v.previous_label)}</dd>` : ""}
    <dt>Exact synonyms</dt><dd>${list(v.synonyms)}</dd>
    ${v.narrow.length ? `<dt>Narrow synonyms</dt><dd>${list(v.narrow)}</dd>` : ""}
    ${v.broad.length ? `<dt>Broad synonyms</dt><dd>${list(v.broad)}</dd>` : ""}
    <dt>Parents</dt><dd>${list(v.parents)}</dd>
    <dt>Definition</dt><dd>${v.definition ? highlight(v.definition, shared) : '<span class="small">—</span>'}</dd></dl></div>`).join("")
    : `<div class="card"><h3>${esc(r.db)} ${esc(r.target_id)}</h3><p class="small">Not found at the source or in the
       local reference indexes. Open the link to check it by hand.</p></div>`;
  const ev = r.evidence;
  const evidence = `<div class="card"><h3>Evidence</h3><dl>
    ${r.judgment === "predicted" ? `<dt>Prediction</dt><dd>${esc(r.comment)}</dd>`
      : `<dt>Curator</dt><dd>${esc(r.curator)}${r.comment ? " — “" + esc(r.comment) + "”" : ""}</dd>`}
    <dt>Supporting cross-references</dt><dd>${ev.support.length ? ev.support.map((s) => `${esc(s.db)} ${esc(s.id)} (${esc(s.origin)}, via ${esc(s.via.join(", "))})`).join("<br>") : "none"}</dd>
    <dt>Cross-references ARI rejected</dt><dd>${ev.against.length ? ev.against.map((s) => `${esc(s.db)} ${esc(s.id)} (via ${esc(s.via.join(", "))})`).join("<br>") : "none"}</dd>
    <dt>Conflicting cross-references</dt><dd>${ev.conflicts.length ? ev.conflicts.map((c) => `${esc(c.db)}: target ${esc(c.target_ids.join(", "))} vs ARI ${esc(c.ari_ids.join(", "))}`).join("<br>") : "none"}</dd>
    <dt>Shared definition words</dt><dd>${shared ? esc(shared.join(", ")) : "—"}</dd></dl></div>`;
  return `<tr class="detail"><td colspan="12"><div class="cmp">${ari}${targets}${evidence}</div></td></tr>`;
}

function exportCSV() {
  const cols = [["Line", (r) => r.line], ["Marked for review", (r) => marks.has(key(r))],
    ["PR line link", (r) => r.line_url], ["ARI id", (r) => r.ari_id], ["ARI label", (r) => r.ari_label], ["Database", (r) => r.db],
    ["Target id", (r) => r.target_id], ["Target label", (r) => r.target_label],
    ["Target label in local index", (r) => r.target_previous_label], ["Direct", (r) => r.direct],
    ["File", (r) => r.file], ["Status", (r) => r.status], ["Previous", (r) => r.previous], ["Judgment", (r) => r.judgment], ["Name match", (r) => r.name_match.label],
    ["ARI name", (r) => r.name_match.ari], ["Target name", (r) => r.name_match.target],
    ["Word overlap", (r) => r.name_match.overlap], ["Definition overlap", (r) => r.definition_overlap?.score ?? ""],
    ["Supporting xrefs", (r) => r.evidence.support.map((s) => `${s.db}:${s.id}`).join("; ")],
    ["Flags", (r) => r.flags.join("; ")], ["Main", (r) => r.on_main], ["Hint", (r) => r.hint],
    ["Score", (r) => r.score], ["Curator", (r) => r.curator], ["Comment", (r) => r.comment],
    ["Note", (r) => notes[key(r)]?.text || ""], ["Google search", (r) => googleURL(r)]];
  const cell = (v) => `"${String(v).replace(/"/g, '""')}"`;
  const csv = [cols.map((c) => cell(c[0])).join(","), ...filtered().map((r) => cols.map((c) => cell(c[1](r))).join(","))].join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], {type: "text/csv"}));
  a.download = `ari-pr-${$("pr").value}-matrix.csv`;
  a.click();
}

$("pr").addEventListener("change", loadMatrix);
$("reload").addEventListener("click", loadMatrix);
["q", "db", "judgment", "kind", "marked"].forEach((id) => $(id).addEventListener("input", render));
$("csv").addEventListener("click", exportCSV);
$("post-review").addEventListener("click", async () => {
  const n = $("pr").value;
  const mine = Object.values(notes).filter((x) => x.by === me && !x.posted).length;
  if (!confirm(`Post your ${mine} unposted note${mine === 1 ? "" : "s"} to PR #${n} on GitHub as @${me}?\n\n` +
      "They go in one review, each as a comment on its row's line; notes on lines outside the " +
      "PR's diff are listed in the review's body. This is public on the PR and can't be undone here.")) return;
  $("post-review").disabled = true;
  $("posted").textContent = "Posting…";
  const res = await sendJSON(`prs/${n}/review`, {});
  if (!res.ok) { $("posted").textContent = `Not posted: ${await failure(res)}`; $("post-review").disabled = false; return; }
  const out = await res.json();
  applyState(out);
  $("posted").innerHTML = `<a href="${esc(out.review_url)}" target="_blank">Review posted ↗</a> ·
    ${out.on_lines} on their lines${out.in_body ? ` · ${out.in_body} in the review body (outside the diff)` : ""}
    ${out.not_posted.length ? ` · ${out.not_posted.length} not posted (row no longer in the PR)` : ""}`;
  render();
});
$("collapse").addEventListener("click", () => {
  const ids = new Set(filtered().map((r) => r.ari_id));
  collapsed = [...ids].every((id) => collapsed.has(id)) ? new Set() : ids;
  render();
});
$("chips").addEventListener("click", (e) => {
  if (e.target.dataset.marked) { $("marked").value = $("marked").value === "yes" ? "" : "yes"; render(); return; }
  const h = e.target.dataset.hint;
  if (h !== undefined) { hintFilter = hintFilter === h ? "" : h; render(); }
});
$("body").addEventListener("change", async (e) => {
  if (!e.target.classList.contains("note")) return;
  const box = e.target, status = box.parentElement.querySelector(".note-status");
  status.textContent = "Saving…";
  const k = box.closest("tr.item").dataset.key;
  const res = await sendJSON(`prs/${$("pr").value}/notes`, {key: k, text: box.value});
  if (!res.ok) { status.textContent = `Not saved: ${await failure(res)}`; return; }
  applyState(await res.json());
  // Not re-rendered, so focus and any further typing elsewhere are kept.
  box.classList.toggle("has-note", Boolean(box.value.trim()));
  status.innerHTML = box.value.trim() ? `Saved · ${noteStatus(notes[k])}` : "Removed";
});
$("body").addEventListener("keydown", (e) => {
  if (e.target.classList.contains("note") && e.key === "Enter" && (e.ctrlKey || e.metaKey)) e.target.blur();
});
$("body").addEventListener("change", async (e) => {
  if (!e.target.classList.contains("mark")) return;
  const k = e.target.closest("tr.item").dataset.key;
  const res = await sendJSON(`prs/${$("pr").value}/marks`, {key: k, marked: e.target.checked});
  if (!res.ok) { alert(`Saving the mark failed: ${await failure(res)}`); e.target.checked = !e.target.checked; return; }
  applyState(await res.json());
  render();
});
$("body").addEventListener("click", async (e) => {
  const copy = e.target.closest(".copy");
  if (copy) {
    await navigator.clipboard.writeText(copy.dataset.copy);
    copy.textContent = "✓";
    setTimeout(() => { copy.textContent = "⧉"; }, 1200);
    return;
  }
  const group = e.target.closest("tr.group");
  if (group) {
    const id = group.dataset.ari;
    collapsed.has(id) ? collapsed.delete(id) : collapsed.add(id);
    render();
    return;
  }
  const tr = e.target.closest("tr.item");
  if (!tr || e.target.closest("a") || e.target.classList.contains("mark") || e.target.closest(".note")) return;
  const k = tr.dataset.key;
  open.has(k) ? open.delete(k) : open.add(k);
  render();
});
document.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => {
  const k = th.dataset.sort;
  sortDir = sortKey === k ? -sortDir : 1; sortKey = k; render();
}));
// Resizable columns: drag a header's right edge; double-click it to restore the default.
// Widths are a per-browser convenience, so storage failures just mean defaults.
const WIDTHS_KEY = "pr-review-column-widths";
const headers = [...document.querySelectorAll("thead th")];
let widths = {};
try { widths = JSON.parse(localStorage.getItem(WIDTHS_KEY)) || {}; } catch { widths = {}; }

function applyWidths() {
  let total = 0;
  headers.forEach((th, i) => {
    const w = widths[i] || Number(th.dataset.w);
    th.style.width = `${w}px`;
    total += w;
  });
  document.querySelector("table").style.width = `${total}px`;
}

function saveWidths() {
  try { localStorage.setItem(WIDTHS_KEY, JSON.stringify(widths)); } catch { /* defaults next time */ }
}

headers.forEach((th, i) => {
  const grip = document.createElement("span");
  grip.className = "resizer";
  grip.title = "Drag to resize · double-click to reset";
  th.appendChild(grip);
  grip.addEventListener("click", (e) => e.stopPropagation());
  grip.addEventListener("dblclick", (e) => {
    e.stopPropagation(); delete widths[i]; saveWidths(); applyWidths();
  });
  grip.addEventListener("pointerdown", (e) => {
    e.preventDefault(); e.stopPropagation();
    const startX = e.clientX, startW = th.getBoundingClientRect().width;
    grip.setPointerCapture(e.pointerId);
    grip.classList.add("active"); document.body.classList.add("resizing");
    const move = (ev) => { widths[i] = Math.max(40, Math.round(startW + ev.clientX - startX)); applyWidths(); };
    const up = () => {
      grip.removeEventListener("pointermove", move);
      grip.classList.remove("active"); document.body.classList.remove("resizing");
      saveWidths();
    };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", up, {once: true});
  });
});
applyWidths();

loadPRs().catch((e) => { $("body").innerHTML = `<tr><td colspan="12" class="empty">${esc(e.message)}</td></tr>`; });
