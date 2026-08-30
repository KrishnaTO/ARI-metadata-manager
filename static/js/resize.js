// Draggable column gutters: let the user resize the left list and the right
// panel. Widths are stored as CSS custom properties on #layout (--left-w /
// --right-w) so they override the default class widths only once set.
//
// The chosen width is this window's. It used to be localStorage alone, shared by
// every window of the browser: size a narrow comparison window to fit and the
// wide one inherited it on its next reload, in a workflow built on two
// differently-shaped windows (issue #114). A window seeds itself from the last
// width set anywhere and then keeps its own.
(function () {
  const layout = document.getElementById('layout');
  if (!layout) return;
  const leftCol = layout.querySelector('.left-col');
  const rightCol = document.getElementById('right-col');
  const KEY_L = 'ari.leftW', KEY_R = 'ari.rightW';
  const LEFT_MIN = 230, RIGHT_MIN = 360;

  // This window's width, seeded once from the shared default.
  function winWidth(key) {
    try {
      const own = sessionStorage.getItem(key);
      if (own !== null) return own;
      const shared = localStorage.getItem(key);
      if (shared !== null) sessionStorage.setItem(key, shared);
      return shared;
    } catch (e) { return null; }
  }
  function setWinWidth(key, value) {
    try {
      sessionStorage.setItem(key, value);      // this window, from now on
      localStorage.setItem(key, value);        // and the default for the next one
    } catch (e) { /* storage may be unavailable */ }
  }

  // Restore any previously chosen widths.
  const l0 = winWidth(KEY_L); if (l0) layout.style.setProperty('--left-w', l0);
  const r0 = winWidth(KEY_R); if (r0) layout.style.setProperty('--right-w', r0);

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
      const l = layout.style.getPropertyValue('--left-w');
      const r = layout.style.getPropertyValue('--right-w');
      if (l) setWinWidth(KEY_L, l.trim());
      if (r) setWinWidth(KEY_R, r.trim());
    }
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  }

  const hL = document.getElementById('resize-left');
  const hR = document.getElementById('resize-right');
  hL?.addEventListener('mousedown', e => startDrag(hL, 'left', e));
  hR?.addEventListener('mousedown', e => startDrag(hR, 'right', e));

  // Double-click a gutter to reset that column to its default width.
  const forget = key => {
    try { sessionStorage.removeItem(key); localStorage.removeItem(key); }
    catch (e) { /* storage may be unavailable */ }
  };
  hL?.addEventListener('dblclick', () => {
    layout.style.removeProperty('--left-w');
    forget(KEY_L);
  });
  hR?.addEventListener('dblclick', () => {
    layout.style.removeProperty('--right-w');
    forget(KEY_R);
  });
})();
