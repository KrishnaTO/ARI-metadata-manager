// Theme before the first paint, so the dashboard does not flash light and then
// go dark. A file rather than inline script: the Content-Security-Policy is
// `script-src 'self'`, which blocks inline script outright.
(function () {
  try {
    var t = localStorage.getItem('ari-theme') || localStorage.getItem('theme');
    if (t) document.documentElement.dataset.theme = t;
    else if (matchMedia('(prefers-color-scheme: dark)').matches)
      document.documentElement.dataset.theme = 'dark';
  } catch (e) { /* storage may be unavailable */ }
})();
