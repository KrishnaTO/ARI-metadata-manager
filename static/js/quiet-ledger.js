// Quiet ledger (design 1a). Small behaviours layered over detail.js and
// trees.js without touching them:
//  1. The definition shows up to three lines with a Show full definition
//     control, added only when the text actually overflows.
//  2. The rail's autoimmune-only filter is no longer offered, so a filter left
//     on from an earlier visit is switched off rather than hiding diseases with
//     no visible control to undo it.
(function(){
  const CARET = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';

  function clampDefinition(){
    const def = document.querySelector('#detail-pane .definition');
    if (!def || def.dataset.qlDone) return;
    def.dataset.qlDone = '1';
    def.classList.add('clamped');
    if (def.scrollHeight <= def.clientHeight + 1){ def.classList.remove('clamped'); return; }
    const row = document.createElement('div');
    row.className = 'def-expand-row';
    row.innerHTML = `<button type="button" class="def-expand" aria-expanded="false"><span>Show full definition</span>${CARET}</button>`;
    def.insertAdjacentElement('afterend', row);
    const btn = row.querySelector('button');
    btn.addEventListener('click', () => {
      const open = def.classList.toggle('clamped') === false;
      btn.setAttribute('aria-expanded', String(open));
      btn.querySelector('span').textContent = open ? 'Show less' : 'Show full definition';
    });
  }

  function clearAutoimmuneFilter(){
    const cb = document.getElementById('ai-only');
    if (cb && cb.checked){
      cb.checked = false;
      cb.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  const pane = document.getElementById('detail-pane');
  if (pane) new MutationObserver(clampDefinition).observe(pane, { childList: true });
  window.addEventListener('load', clearAutoimmuneFilter);

  // 3. Mobile (≤640px, mockups 2a–2d): the header search collapses to an icon
  //    that opens the field across the bar, and the rail drawer carries the
  //    header links that no longer fit.
  const mobile = window.matchMedia('(max-width: 640px)');
  const header = document.querySelector('header');
  const wrap = document.querySelector('.search-wrap');
  const input = document.getElementById('search');
  if (header && wrap && input){
    wrap.addEventListener('click', () => {
      if (!mobile.matches || header.classList.contains('search-open')) return;
      header.classList.add('search-open');
      input.focus();
    });
    const close = () => { if (!input.value) header.classList.remove('search-open'); };
    input.addEventListener('blur', () => setTimeout(close, 150));
    input.addEventListener('keydown', e => { if (e.key === 'Escape'){ input.value = ''; input.blur(); header.classList.remove('search-open'); } });
    mobile.addEventListener('change', () => header.classList.remove('search-open'));
  }
  const rail = document.getElementById('disease-index');
  const matrix = document.getElementById('matrix-link');
  if (rail && matrix){
    const links = document.createElement('div');
    links.className = 'rail-links';
    links.innerHTML = '<a href="' + matrix.getAttribute('href') + '">Cross-references</a>' +
      '<button type="button" class="rl-submit">Submit for review</button>';
    links.querySelector('.rl-submit').addEventListener('click', () => document.querySelector('#gh-slot .hbtn.primary')?.click());
    rail.appendChild(links);
  }
})();
