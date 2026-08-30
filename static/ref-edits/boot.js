// Applies the curator's saved display preferences to <html> before the page
// paints, so there is no flash of the default theme or density.
//
// This was an inline <script>. Tightening the Content Security Policy to
// `script-src 'self'` (when the CDN libraries were vendored) silently blocked
// it, and the review page began ignoring saved theme, density and legend
// settings entirely. Kept as a real file so the policy needs no 'unsafe-inline'
// and no hash that would break the moment this is edited.
//
// Loaded from <head> without defer/async: it must run before first paint.
try {
  var t = localStorage.getItem('theme');
  if (t) document.documentElement.dataset.theme = t;
  else if (matchMedia('(prefers-color-scheme: dark)').matches) document.documentElement.dataset.theme = 'dark';
  document.documentElement.dataset.density = localStorage.getItem('refDensity') || 'comfortable';
  document.documentElement.dataset.legend = localStorage.getItem('refLegend') || 'on';
} catch (e) {
  // Private mode, or storage disabled: the CSS defaults are correct anyway.
}
