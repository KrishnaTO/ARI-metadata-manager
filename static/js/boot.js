// Pre-paint preferences: theme and text size.
//
// Both decide what the first frame looks like, so they are applied here — in
// <head>, before the stylesheet paints — rather than by main.js at the end of
// the body, which rendered the app light and at standard size and then jumped.
// A file rather than an inline <script> because the Content-Security-Policy is
// `script-src 'self'`, which blocks inline script outright.
//
// main.js still owns the theme control and re-applies the same value; this only
// decides what is on screen before it runs.
(function () {
  var root = document.documentElement;
  var get = function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } };
  var theme = get('ari-theme');
  if (theme) root.setAttribute('data-theme', theme);
  else if (matchMedia('(prefers-color-scheme: dark)').matches) root.setAttribute('data-theme', 'dark');
  root.dataset.textsize = get('ari-textsize') || 'standard';
})();
