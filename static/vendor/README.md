# Vendored runtime assets

These were loaded from jsDelivr with SRI pinning. The pinning is exactly right
for **integrity** — but it does nothing for **availability**, and hospital,
university and pharmaceutical networks block third-party CDNs routinely. The
failure mode was a broken pathophysiology graph and a broken word cloud, with
nothing on screen explaining why (issue #122).

Serving them from the app also removes the CDN exception from the Content
Security Policy, so `script-src` is now `'self'` alone.

| File | Package | Version | Upstream |
|---|---|---|---|
| `chart.umd.min.js` | chart.js | 4.5.1 | https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js |
| `d3.min.js` | d3 | 7.9.0 | https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js |
| `d3.layout.cloud.js` | d3-cloud | 1.2.7 | https://cdn.jsdelivr.net/npm/d3-cloud@1.2.7/build/d3.layout.cloud.js |

Each file was verified byte-for-byte against the SHA-384 the page previously
pinned, so what is committed here is what was being served.

## Refreshing

Download the new version, then check its digest before committing it:

```bash
openssl dgst -sha384 -binary static/vendor/<file> | openssl base64 -A
```

Compare that against the digest published for the release, and update the table
above. Do not commit a file whose digest you have not checked.
