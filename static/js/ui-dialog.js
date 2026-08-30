// Inline dialogs and announcements, shared by both pages.
//
// Seventeen browser-native dialogs sat in the daily curation path — twelve
// `alert()`, two `confirm()` and one `prompt()` on the review page, four more
// `confirm()` on the record page (issue #118). They block the page, land
// centre-screen over the thing they describe, cannot be styled or read in
// context by a screen reader, and are disabled outright in some embedded
// browsers. The pull-request comment in particular was collected with
// `window.prompt()`: single-line, unstyled, and easy to dismiss by accident.
//
// Everything here uses the native <dialog> element, which gives focus trapping,
// Escape-to-close and an inert backdrop without reimplementing them.
//
// Loaded as a classic script before the page's own scripts.

(function (root) {
  'use strict';

  function el(html) {
    const t = document.createElement('template');
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  }

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // ------------------------------------------------------------ announcing
  // A single polite live region. Screen readers need one that exists *before*
  // the message is written into it, so it is created once and reused — there
  // was no live region anywhere in the app.
  let liveRegion = null;
  function live() {
    if (!liveRegion) {
      liveRegion = el('<div class="sr-only" role="status" aria-live="polite" aria-atomic="true"></div>');
      document.body.appendChild(liveRegion);
    }
    return liveRegion;
  }

  // Finish a dialog exactly once and hand back the chosen value.
  //
  // Deliberately does NOT use the dialog's `close` event. The embedded browser
  // this app is tested in never fires it — not for a `method="dialog"` form
  // submission, and not even for an explicit `close()` call — although it does
  // set `returnValue` and `open = false`. A promise wired to `close` therefore
  // hangs forever and the caller's flow stops dead.
  //
  // A dialog has exactly two exits, so both are wired directly:
  //   * the form submitting, which carries the pressed button in `submitter`
  //   * Escape, which is handled here rather than left to the UA's `cancel`
  //     event (also unreliable here)
  function wireDialog(dlg, resolve, valueFor) {
    let done = false;
    const settle = outcome => {
      if (done) return;
      done = true;
      try { if (dlg.open) dlg.close(); } catch (e) { /* already closed */ }
      const value = valueFor(outcome);
      dlg.remove();
      resolve(value);
    };
    dlg.querySelector('form').addEventListener('submit', e => {
      e.preventDefault();                       // we close it ourselves
      settle(e.submitter ? e.submitter.value : 'cancel');
    });
    dlg.addEventListener('keydown', e => {
      if (e.key === 'Escape') { e.preventDefault(); settle('cancel'); }
    });
    return settle;
  }

  function announce(message) {
    const r = live();
    r.textContent = '';                 // re-announce an identical message
    setTimeout(() => { r.textContent = message; }, 30);
  }

  // ---------------------------------------------------------------- confirm
  // Returns a promise for the answer. `danger` styles the primary action as
  // destructive; `detail` is optional supporting text under the question.
  function confirmDialog({ title, detail = '', confirmLabel = 'Confirm',
                           cancelLabel = 'Cancel', danger = false } = {}) {
    return new Promise(resolve => {
      const dlg = el(`<dialog class="ui-dialog">
        <form method="dialog" class="ui-dialog-form">
          <h2 class="ui-dialog-title">${esc(title)}</h2>
          ${detail ? `<p class="ui-dialog-detail">${esc(detail)}</p>` : ''}
          <div class="ui-dialog-actions">
            <button value="cancel" class="ui-btn">${esc(cancelLabel)}</button>
            <button value="ok" class="ui-btn primary${danger ? ' danger' : ''}">${esc(confirmLabel)}</button>
          </div>
        </form>
      </dialog>`);
      document.body.appendChild(dlg);
      wireDialog(dlg, resolve, outcome => outcome === 'ok');
      dlg.showModal();
      // Focus the safe choice, so Enter never destroys anything by reflex.
      dlg.querySelector('button[value="cancel"]').focus();
    });
  }

  // ------------------------------------------------------------------ text
  // Replaces window.prompt(). `multiline` gives a real textarea — the
  // pull-request comment deserves more than one unstyled line.
  function textDialog({ title, detail = '', label = '', value = '', placeholder = '',
                        confirmLabel = 'Save', multiline = false, required = false } = {}) {
    return new Promise(resolve => {
      const field = multiline
        ? `<textarea id="ui-dialog-input" class="ui-dialog-input" rows="4"
              placeholder="${esc(placeholder)}">${esc(value)}</textarea>`
        : `<input id="ui-dialog-input" class="ui-dialog-input" type="text"
              value="${esc(value)}" placeholder="${esc(placeholder)}">`;
      const dlg = el(`<dialog class="ui-dialog">
        <form method="dialog" class="ui-dialog-form">
          <h2 class="ui-dialog-title">${esc(title)}</h2>
          ${detail ? `<p class="ui-dialog-detail">${esc(detail)}</p>` : ''}
          ${label ? `<label class="ui-dialog-label" for="ui-dialog-input">${esc(label)}</label>` : ''}
          ${field}
          <div class="ui-dialog-actions">
            <button value="cancel" class="ui-btn">Cancel</button>
            <button value="ok" class="ui-btn primary">${esc(confirmLabel)}</button>
          </div>
        </form>
      </dialog>`);
      document.body.appendChild(dlg);
      const input = dlg.querySelector('#ui-dialog-input');
      const okBtn = dlg.querySelector('button[value="ok"]');

      const sync = () => { okBtn.disabled = required && !input.value.trim(); };
      input.addEventListener('input', sync);
      sync();

      // Enter submits a single-line field; a textarea keeps Enter for newlines
      // and takes Ctrl/Cmd+Enter instead.
      input.addEventListener('keydown', e => {
        const submit = multiline ? (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) : e.key === 'Enter';
        if (submit && !okBtn.disabled) { e.preventDefault(); okBtn.click(); }
      });

      // Read the field before the node is removed.
      wireDialog(dlg, resolve, outcome => (outcome === 'ok' ? input.value : null));
      dlg.showModal();
      input.focus();
      input.select?.();
    });
  }

  // ------------------------------------------------------------ validation
  // Validation used to arrive as a 2.6-second toast at the bottom of the
  // viewport, with no connection to the field it described, nothing announced,
  // and — because the form returned on the first failure — one message per
  // submit attempt for a user missing four required fields (issue #99).
  //
  // `errors` is [{ id, message }] naming the input each failure belongs to.
  // Renders a summary with role="alert" at the top of `container`, marks each
  // input aria-invalid, and wires aria-describedby to its own message.
  // Returns true when there were no errors.
  function showFieldErrors(container, errors, summaryId = 'form-error-summary') {
    clearFieldErrors(container, summaryId);
    if (!errors.length) return true;

    for (const { id, message } of errors) {
      const input = container.querySelector('#' + CSS.escape(id));
      if (!input) continue;
      input.setAttribute('aria-invalid', 'true');
      const msgId = id + '-error';
      input.setAttribute('aria-describedby', msgId);
      const msg = el(`<span class="field-error" id="${esc(msgId)}">${esc(message)}</span>`);
      // After the control, or after its wrapper when the control sits in one.
      (input.closest('.field, .so-field') || input).after(msg);
      // Clearing the field clears its error, so the form stops nagging.
      input.addEventListener('input', function once() {
        input.removeAttribute('aria-invalid');
        input.removeAttribute('aria-describedby');
        document.getElementById(msgId)?.remove();
        input.removeEventListener('input', once);
      });
    }

    const summary = el(`<div class="form-errors" id="${esc(summaryId)}" role="alert" tabindex="-1">
      <h3>${errors.length === 1 ? 'One thing needs fixing' : errors.length + ' things need fixing'}</h3>
      <ul>${errors.map(e =>
        `<li><a href="#${esc(e.id)}" data-goto="${esc(e.id)}">${esc(e.message)}</a></li>`).join('')}</ul>
    </div>`);
    container.prepend(summary);
    summary.querySelectorAll('[data-goto]').forEach(a =>
      a.addEventListener('click', ev => {
        ev.preventDefault();
        const t = container.querySelector('#' + CSS.escape(a.dataset.goto));
        t?.focus();
        t?.scrollIntoView({ block: 'center' });
      }));
    summary.focus();
    announce(errors.length === 1
      ? errors[0].message
      : `${errors.length} things need fixing before this can be saved.`);
    return false;
  }

  function clearFieldErrors(container, summaryId = 'form-error-summary') {
    container.querySelector('#' + CSS.escape(summaryId))?.remove();
    container.querySelectorAll('.field-error').forEach(e => e.remove());
    container.querySelectorAll('[aria-invalid="true"]').forEach(i => {
      i.removeAttribute('aria-invalid');
      i.removeAttribute('aria-describedby');
    });
  }

  root.UIDialog = { confirm: confirmDialog, text: textDialog, announce,
                    showFieldErrors, clearFieldErrors };
  if (typeof module !== 'undefined' && module.exports) root.UIDialog.__esc = esc;
})(typeof globalThis !== 'undefined' ? globalThis : this);
