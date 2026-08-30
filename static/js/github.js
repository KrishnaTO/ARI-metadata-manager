// Per-user GitHub publish control. Talks to the backend (which holds the OAuth
// secret and the user's token server-side); no secrets are exposed here.
// Injects a sign-in / "Publish to GitHub" control into the header and publishes
// the current ontology as a PR on a branch named after the disease.
(function () {
  let ghUser = null;

  function el(html) { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstChild; }

  // Two-letter avatar: the initials of the display name when there is one,
  // otherwise the first two characters of the login.
  function initials(u) {
    const parts = String(u.name || '').trim().split(/\s+/).filter(Boolean);
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return String(u.name || u.login || '?').replace(/[^a-z0-9]/gi, '').slice(0, 2).toUpperCase();
  }

  async function refresh() {
    let me;
    try { me = await api('/api/v2/me'); } catch (e) { return; }
    state.githubEnabled = !!me.github_enabled;
    state.authenticated = !!me.authenticated;
    ghUser = me.authenticated ? me : null;
    state.githubName = ghUser ? (ghUser.name || ghUser.login) : null;
    state.githubLogin = ghUser ? ghUser.login : null;
    if (typeof resolveEditor === 'function') resolveEditor();
    // Whether curating is allowed depends on this answer, so re-check the
    // Curate segment once it has landed.
    syncCurateAccess();
    if (!me.github_enabled) return;          // feature off -> no identity control
    render();
  }

  // The toolbar carries only who you are and the publish action; signing out is
  // a once-a-session act, so it sits in the preferences popover's account section.
  function render() {
    const slot = document.getElementById('gh-slot');
    const account = document.getElementById('menu-account');
    if (!slot || !account) return;
    slot.innerHTML = ''; account.innerHTML = '';
    if (ghUser) {
      slot.appendChild(el(`<span class="who"><span class="avatar">${esc(initials(ghUser))}</span>${esc(ghUser.login)}</span>`));
      const pub = el(`<button class="hbtn primary" title="Send your changes for review — nothing goes live until someone accepts them">${Words.publishShort}</button>`);
      pub.addEventListener('click', publish);
      slot.appendChild(pub);
      account.appendChild(el('<div class="menu-sep"></div>'));
      account.appendChild(el('<div class="menu-h">Account</div>'));
      const out = el('<button class="menu-item">Sign out</button>');
      out.addEventListener('click', async () => {
        closeAppMenu();
        await api('/api/v2/logout', { method: 'POST' });
        ghUser = null; state.githubName = null; state.githubLogin = null;
        state.authenticated = false;
        if (typeof resolveEditor === 'function') resolveEditor();
        syncCurateAccess();
        render();
      });
      account.appendChild(out);
    } else {
      const login = el('<button class="hbtn">Sign in with GitHub</button>');
      login.addEventListener('click', () => (location.href = BASE_PATH + '/auth/github'));
      slot.appendChild(login);
    }
  }

  // What is in this pull request, listed. The dialog used to show a free-text
  // title box and nothing else: a curator could not see which diseases their
  // submission carried, and the default title named whichever record happened to
  // be on screen — so three edits published under the name of one of them, and a
  // brand-new disease read as an update (issue #25).
  function changeListHtml(pending) {
    const changes = (pending && pending.changes) || [];
    if (pending && pending.unavailable) {
      return `<p class="pub-note">The list of changed diseases is unavailable — the ` +
        `source branch could not be read. Your changes still publish in full.</p>`;
    }
    if (!changes.length) {
      return `<p class="pub-note">No disease changes found against ` +
        `<code>${esc(pending?.source_branch || 'the source branch')}</code>. ` +
        `Cross-reference verdicts are published from the review page.</p>`;
    }
    const rows = changes.map(c => {
      const what = c.removed ? 'removed'
        : c.is_new ? 'new disease'
        : c.fields.map(f => f.label).join(', ');
      return `<li><span class="pub-d">${esc(c.name)}</span>` +
        `<span class="pub-id">${esc(c.ari_id)}</span>` +
        `<span class="pub-w">${esc(what)}</span></li>`;
    }).join('');
    return `<div class="field"><label>In this submission (${changes.length})</label>
      <ul class="pub-list">${rows}</ul>
      <p class="pub-note">Everything you have changed goes in together — a pull request
        carries the whole ontology file, so diseases cannot be published separately.</p></div>`;
  }

  async function publish() {
    let pending = null;
    try { pending = await api('/api/v2/pending-changes'); }
    catch (e) { console.warn('Could not list pending changes:', e.message); }
    const def = (pending && pending.title) || 'Update ontology';
    const disease = state.detail?.name || '';
    const m = el(`<div class="modal-overlay" id="pub-overlay"><div class="modal">
      <div class="modal-head"><h2>${esc(Words.publish)}</h2><button class="hbtn" id="pub-close">Close</button></div>
      <div class="modal-body">
        <p style="font-size:13px;margin:0 0 10px">Your changes are sent as a ${esc(Words.submission)} with a summary of what changed (previous &rarr; new). Nothing goes live until someone reviews and accepts it.</p>
        ${changeListHtml(pending)}
        <div class="field"><label>Commit message / PR title</label><input id="pub-msg" value="${esc(def)}"></div>
        <div class="field"><label>Comments (optional)</label><textarea id="pub-comment" placeholder="Why this change, sources, notes for reviewers..."></textarea></div>
        <div class="edit-actions" style="margin-top:4px"><button class="hbtn primary" id="pub-go">${esc(Words.publish)}</button>
          <button class="hbtn" id="pub-cancel">Cancel</button></div>
      </div></div></div>`);
    document.body.appendChild(m);
    const close = () => $('#pub-overlay').remove();
    $('#pub-close').addEventListener('click', close);
    $('#pub-cancel').addEventListener('click', close);
    $('#pub-overlay').addEventListener('click', e => { if (e.target.id === 'pub-overlay') close(); });
    $('#pub-go').addEventListener('click', async () => {
      const message = $('#pub-msg').value.trim();
      const comment = $('#pub-comment').value.trim();
      $('#pub-go').disabled = true; $('#pub-go').textContent = Words.publishing;
      try {
        const r = await api('/api/v2/publish', { method: 'POST', body: { disease, message, comment } });
        close();
        const link = el(`<div class="toast" style="cursor:pointer">${esc(Words.submissionName(r.pr_number))} sent for review — click to view it on GitHub</div>`);
        link.addEventListener('click', () => window.open(r.pr_url, '_blank'));
        document.body.appendChild(link); setTimeout(() => link.remove(), 8000);
      } catch (e) {
        toastError(explainError(e, 'Could not send it for review'));
        $('#pub-go').disabled = false; $('#pub-go').textContent = Words.publish;
      }
    });
  }

  if (document.readyState !== 'loading') refresh();
  else document.addEventListener('DOMContentLoaded', refresh);
})();
