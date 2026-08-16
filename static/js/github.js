// Per-user GitHub publish control. Talks to the backend (which holds the OAuth
// secret and the user's token server-side); no secrets are exposed here.
// Injects a sign-in / "Publish to GitHub" control into the header and publishes
// the current ontology as a PR on a branch named after the disease.
(function () {
  let ghUser = null;

  function el(html) { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstChild; }

  async function refresh() {
    let me;
    try { me = await api('/api/v2/me'); } catch (e) { return; }
    if (!me.github_enabled) return;          // feature off -> show nothing
    ghUser = me.authenticated ? me : null;
    state.githubName = ghUser ? (ghUser.name || ghUser.login) : null;
    state.githubLogin = ghUser ? ghUser.login : null;
    if (typeof resolveEditor === 'function') resolveEditor();
    render();
  }

  // The toolbar carries only who you are and the publish action; signing out is
  // a once-a-session act, so it sits in the ⚙ popover's account section.
  function render() {
    const slot = document.getElementById('gh-slot');
    const account = document.getElementById('menu-account');
    if (!slot || !account) return;
    slot.innerHTML = ''; account.innerHTML = '';
    if (ghUser) {
      slot.appendChild(el(`<span class="who">@${esc(ghUser.login)}</span>`));
      const pub = el('<button class="hbtn primary" title="Commit current ontology to GitHub as a pull request">&#11014; Publish</button>');
      pub.addEventListener('click', publish);
      slot.appendChild(pub);
      account.appendChild(el('<div class="menu-sep"></div>'));
      account.appendChild(el('<div class="menu-h">Account</div>'));
      const out = el('<button class="menu-item">Sign out</button>');
      out.addEventListener('click', async () => {
        closeAppMenu();
        await api('/api/v2/logout', { method: 'POST' });
        ghUser = null; state.githubName = null; state.githubLogin = null;
        if (typeof resolveEditor === 'function') resolveEditor();
        render();
      });
      account.appendChild(out);
    } else {
      const login = el('<button class="hbtn">Sign in with GitHub</button>');
      login.addEventListener('click', () => (location.href = BASE_PATH + '/auth/github'));
      slot.appendChild(login);
    }
  }

  function publish() {
    const disease = state.detail?.name || '';
    const def = disease ? `Update ${disease}` : 'Update ontology';
    const m = el(`<div class="modal-overlay" id="pub-overlay"><div class="modal">
      <div class="modal-head"><h2>&#11014; Publish to GitHub</h2><button class="hbtn" id="pub-close">✕</button></div>
      <div class="modal-body">
        <p style="font-size:13px;margin:0 0 10px">Opens a pull request with a summary of your changes (previous &rarr; new values).</p>
        <div class="field"><label>Commit message / PR title</label><input id="pub-msg" value="${esc(def)}"></div>
        <div class="field"><label>Comments (optional)</label><textarea id="pub-comment" placeholder="Why this change, sources, notes for reviewers..."></textarea></div>
        <div class="edit-actions" style="margin-top:4px"><button class="hbtn primary" id="pub-go">Open pull request</button>
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
      $('#pub-go').disabled = true; $('#pub-go').textContent = 'Publishing…';
      try {
        const r = await api('/api/v2/publish', { method: 'POST', body: { disease, message, comment } });
        close();
        const link = el(`<div class="toast" style="cursor:pointer">PR #${r.pr_number} opened on branch ${esc(r.branch)} — click to open</div>`);
        link.addEventListener('click', () => window.open(r.pr_url, '_blank'));
        document.body.appendChild(link); setTimeout(() => link.remove(), 8000);
      } catch (e) {
        toast('Publish failed: ' + e.message);
        $('#pub-go').disabled = false; $('#pub-go').textContent = 'Open pull request';
      }
    });
  }

  if (document.readyState !== 'loading') refresh();
  else document.addEventListener('DOMContentLoaded', refresh);
})();
