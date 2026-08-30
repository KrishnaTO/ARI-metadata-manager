// One help panel, opened from the ? in either page's header.
//
// Searching both pages for help, guide, tutorial, onboarding or tour returned
// nothing (issue #119). The workflow asks a curator to judge whether an external
// id denotes the same disease, to know when "no term" is the right answer rather
// than leaving a cell blank, to understand why the ✓ is withheld from them
// specifically, and to know what submitting does to a repository they have never
// seen. None of that was written down anywhere they could reach it.
//
// Loaded by both pages; the panel is built on first open and reused after.
(function (root) {
  'use strict';

  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const W = root.Words || { publish: 'Submit for review', submission: 'submission' };

  const SECTIONS = [
    ['What this tool is for', `
      <p>The Autoimmune Registry keeps one record per autoimmune disease. Two jobs
      happen here: describing a disease (its definition, symptoms, prevalence and
      so on), and matching it to the same disease in other databases — SNOMED,
      MONDO, ICD-10 and the rest — so records can be linked across systems.</p>
      <p>Nothing you do here changes the published registry directly. Your work is
      collected and <strong>${esc(W.publish.toLowerCase())}</strong>, and a second
      person merges it.</p>`],

    ['What the symbols in the grid mean', `
      <table class="help-glyphs">
        <tr><td>✓</td><td><strong>Confirmed.</strong> Every id in this cell has been judged
          to name the same disease.</td></tr>
        <tr><td>•</td><td><strong>On file, not yet judged.</strong> Someone recorded an id;
          nobody has said whether it is right.</td></tr>
        <tr><td>!</td><td><strong>Proposed.</strong> The tool found a term whose name is
          exactly this disease's name. It is a suggestion, not a decision.</td></tr>
        <tr><td>?</td><td><strong>Proposed from a synonym.</strong> Weaker: only one of the
          disease's alternative names matched, and those sometimes name a related
          but different condition.</td></tr>
        <tr><td>✕</td><td><strong>Rejected.</strong> A curator judged this id wrong for
          this disease.</td></tr>
        <tr><td>–</td><td><strong>No term.</strong> A curator checked and that database has
          no entry for this disease at all. This is a real answer, and different
          from leaving the cell blank.</td></tr>
        <tr><td></td><td><strong>Blank.</strong> Nobody has looked yet.</td></tr>
      </table>
      <p>A small number after a symbol is how many ids are in that cell. A cell only
      reads ✓ once every id in it has been judged.</p>`],

    ['Why you sometimes cannot confirm a mapping', `
      <p>Whoever adds an id may not be the person who confirms it. A mapping between
      two databases is a claim other people will rely on, so a second curator always
      vouches for it.</p>
      <p>When you added an id yourself, the ✓ is withheld and the panel says who
      added it. You can still reject it, or record that the database has no term —
      only the confirmation is withheld.</p>
      <p>The <strong>Needs me</strong> filter lists the ids other curators added that
      nobody has judged: those are the ones waiting on you.</p>`],

    ['When to use “no term” rather than leaving a cell blank', `
      <p>Blank means <em>nobody has looked</em>. "No term" means <em>somebody looked and
      there is nothing there</em>. The second is a finding worth recording — it stops
      the next curator repeating the search, and it is published as a real statement
      about that database.</p>`],

    [`What ${esc(W.publish.toLowerCase())} does`, `
      <p>It gathers the work you have not sent yet and opens a <strong>${esc(W.submission)}</strong>
      — a proposed change to the registry's data files, with a summary of what
      changed. Nothing is live until someone reviews and accepts it.</p>
      <p>You can keep working afterwards. Later work goes into the same
      ${esc(W.submission)} unless you start a new one.</p>`],

    ['Getting your work back after closing the tab', `
      <p>Verdicts are saved to the server as you record them, so signing in again
      brings them back. Unsaved edits to a disease record are kept in this browser
      and offered back when you reopen that record.</p>
      <p>What is <em>not</em> saved is anything typed into a form and never submitted
      on a different computer — the draft lives in the browser you typed it in.</p>`],
  ];

  let panel = null;

  function build() {
    const wrap = document.createElement('div');
    wrap.className = 'help-overlay';
    wrap.id = 'help-overlay';
    wrap.innerHTML = `<div class="help-panel" role="dialog" aria-modal="true" aria-labelledby="help-title">
      <div class="help-head">
        <h2 id="help-title">How this works</h2>
        <button class="help-close" id="help-close" aria-label="Close help">✕</button>
      </div>
      <div class="help-body">${SECTIONS.map(([h, body]) =>
        `<section><h3>${esc(h)}</h3>${body}</section>`).join('')}</div>
    </div>`;
    document.body.appendChild(wrap);
    wrap.addEventListener('click', e => { if (e.target === wrap) close(); });
    wrap.querySelector('#help-close').addEventListener('click', close);
    return wrap;
  }

  function close() {
    if (panel) panel.classList.remove('open');
    document.removeEventListener('keydown', onKey);
  }

  function onKey(e) { if (e.key === 'Escape') close(); }

  function open() {
    panel = panel || build();
    panel.classList.add('open');
    panel.querySelector('.help-body').scrollTop = 0;
    panel.querySelector('#help-close').focus();
    document.addEventListener('keydown', onKey);
  }

  root.Help = { open, close };
})(typeof globalThis !== 'undefined' ? globalThis : this);
