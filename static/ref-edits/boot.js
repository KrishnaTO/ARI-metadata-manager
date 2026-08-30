// Pre-paint preferences: theme, row density, the glyph legend and the text size.
//
// These four decide what the first frame looks like, so they have to be applied
// before it is painted — otherwise the page renders light, comfortable and at
// standard size and then jumps. This ran as an inline <script> until the
// Content-Security-Policy tightened to `script-src 'self'`, which blocks inline
// script outright: every saved preference was being silently ignored. It is a
// file now, so the policy allows it and the preferences work again.
//
// Loaded in <head>, before the stylesheet has anything to say about the body.
(function () {
  var root = document.documentElement;
  var get = function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } };

  var theme = get('theme');
  if (theme) root.dataset.theme = theme;
  else if (matchMedia('(prefers-color-scheme: dark)').matches) root.dataset.theme = 'dark';

  root.dataset.density = get('refDensity') || 'comfortable';
  // On by default. The objection in issue #97 was the cost, not the content: as
  // a run-on paragraph the legend took 88px — 18% of a zoomed 768px viewport —
  // on every screen, forever. Rebuilt as a single 37px line it costs 5%, and a
  // curator who does not yet know what the seven symbols mean should not have
  // to find a setting to be told (issue #119).
  root.dataset.legend = get('refLegend') || 'on';
  root.dataset.textsize = get('ari-textsize') || 'standard';
})();
