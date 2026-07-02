// Draggable column gutters: let the user resize the left list and the right
// panel. Widths are stored as CSS custom properties on #layout (--left-w /
// --right-w) so they override the default class widths only once set, and are
// persisted to localStorage so the choice survives reloads.
(function () {
  const layout = document.getElementById('layout');
  if (!layout) return;
  const leftCol = layout.querySelector('.left-col');
  const rightCol = document.getElementById('right-col');
  const KEY_L = 'ari.leftW', KEY_R = 'ari.rightW';
  const LEFT_MIN = 230, RIGHT_MIN = 360;

  // Restore any previously chosen widths.
  try {
    const l = localStorage.getItem(KEY_L); if (l) layout.style.setProperty('--left-w', l);
    const r = localStorage.getItem(KEY_R); if (r) layout.style.setProperty('--right-w', r);
  } catch (e) { /* storage may be unavailable */ }

  const clamp = (v, min, max) => Math.max(min, Math.min(v, max));

  function startDrag(handle, which, ev) {
    ev.preventDefault();
    const startX = ev.clientX;
    const total = layout.getBoundingClientRect().width;
    const leftStart = leftCol.getBoundingClientRect().width;
    const rightStart = rightCol.getBoundingClientRect().width;
    layout.classList.add('resizing');
    document.body.classList.add('resizing-cols');
    handle.classList.add('dragging');

    function move(e) {
      const dx = e.clientX - startX;
      if (which === 'left') {
        const w = clamp(Math.round(leftStart + dx), LEFT_MIN, Math.round(total * 0.6));
        layout.style.setProperty('--left-w', w + 'px');
      } else {
        // Dragging the right gutter leftwards widens the right panel.
        const w = clamp(Math.round(rightStart - dx), RIGHT_MIN, Math.round(total * 0.8));
        layout.style.setProperty('--right-w', w + 'px');
      }
    }
    function up() {
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
      layout.classList.remove('resizing');
      document.body.classList.remove('resizing-cols');
      handle.classList.remove('dragging');
      try {
        const l = layout.style.getPropertyValue('--left-w');
        const r = layout.style.getPropertyValue('--right-w');
        if (l) localStorage.setItem(KEY_L, l.trim());
        if (r) localStorage.setItem(KEY_R, r.trim());
      } catch (e) { /* ignore */ }
    }
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  }

  const hL = document.getElementById('resize-left');
  const hR = document.getElementById('resize-right');
  hL?.addEventListener('mousedown', e => startDrag(hL, 'left', e));
  hR?.addEventListener('mousedown', e => startDrag(hR, 'right', e));

  // Double-click a gutter to reset that column to its default width.
  hL?.addEventListener('dblclick', () => {
    layout.style.removeProperty('--left-w');
    try { localStorage.removeItem(KEY_L); } catch (e) { /* ignore */ }
  });
  hR?.addEventListener('dblclick', () => {
    layout.style.removeProperty('--right-w');
    try { localStorage.removeItem(KEY_R); } catch (e) { /* ignore */ }
  });
})();
