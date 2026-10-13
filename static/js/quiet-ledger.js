// Quiet ledger (design 1a). Two small behaviours layered over detail.js and
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
})();
