# Operations: the changes that must be applied on the server

Everything else in the review batch ships as code and takes effect on the next
deploy. The tasks below **cannot** be done from a pull request — they change
configuration, secrets, or infrastructure on the Lightsail box. Do them in this
order; each is independent apart from where noted.

Run all of these on the app host, from `/opt/ari/ari-metadata-manager`.

---

## Before you start

The deploy timer will pull and restart underneath you while you work. Stop it
first, and start it again at the end:

```bash
sudo systemctl stop ari-mm-update.timer
```

---

## 1. Set `SESSION_SECRET` — REQUIRED, the app will not start without it

**Issue #110. Do this before deploying the `security-hardening` branch.**

`SESSION_SECRET` was optional and fell back to a per-process random key, so every
restart signed all curators out — and the deploy timer restarts every ten
minutes. The app now refuses to start when GitHub sign-in is configured and this
is unset, rather than silently generating one.

Check whether it is already set and is a real value:

```bash
grep -c '^SESSION_SECRET=replace_with_64_hex_chars$' /opt/ari/ari-metadata-manager/.env
```

If that prints `1`, it is still the placeholder. Generate a real one:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Put it in `.env` as `SESSION_SECRET=<the value>` — **bare, with no quotes**.
(Quoted values used to load with the quotes included; that is fixed, but bare is
still what the file expects everywhere else.)

Changing this value signs everyone out once. That is the last time it should
happen.

---

## 2. Set `ALLOWED_LOGINS`

**Issue #113.** It defaults to empty, which means any GitHub user with repo
access can sign in. Every sign-in while it is unset is now logged at `WARNING`,
so you can see who has been using it:

```bash
sudo journalctl -u ari-mm --since "30 days ago" | grep "ALLOWED_LOGINS unset"
```

Then add the curators to `.env`:

```bash
ALLOWED_LOGINS=KrishnaTO,Jennyzeng25
```

## 3. Set `ASSIGN_ADMINS`

**Issue #113.** Cutting a release archives every non-kept feedback entry, and it
is now restricted to this list. If it is empty, any signed-in curator can still
do it — the same as before. Set it:

```bash
ASSIGN_ADMINS=KrishnaTO
```

---

## 4. Re-authorise the GitHub OAuth App at the narrower scope

**Issue #106.** Sign-in asked for `repo` — full read/write over every repository
each curator can reach, including their employer's private ones. It now asks for
`public_repo`.

Existing tokens keep the scope they were granted with. Narrowing it only takes
effect when a curator re-authorises, so:

1. Deploy the `security-hardening` branch first.
2. Ask each curator to revoke the app at
   <https://github.com/settings/applications> → *Authorized OAuth Apps* → ARI
   Metadata Manager → **Revoke**.
3. They sign in again and will see `public_repo` on the consent screen.

Until they do, the old broad tokens remain valid and remain in `.sessions.json`.
Deleting that file forces everyone to sign in again but does **not** revoke the
tokens on GitHub's side — only the curator can do that, at the link above.

> If the ontology repository is private, `public_repo` is not enough and sign-in
> will fail. In that case keep `repo` and move to a GitHub App scoped to the one
> repository instead; that is the durable fix either way.

---

## 5. Apply the nginx configuration

**Issues #113, #122.** Adds the security headers, the rate-limit zones and the
`/healthz` location.

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/ari-mm
sudo nginx -t
```

`nginx -t` must print `syntax is ok` and `test is successful`. Only then:

```bash
sudo systemctl reload nginx
```

Verify the headers arrive:

```bash
curl -sSI https://aurint.ca/ari-editor/ | grep -Ei 'strict-transport|x-frame|x-content-type|referrer-policy'
```

And that the health check answers:

```bash
curl -sS https://aurint.ca/ari-editor/healthz
```

**Rate limits.** The API zone is 20 req/s with a burst of 40 per IP; `/publish`
is 6 req/min with a burst of 3. If curators sit behind one institutional NAT they
share an IP and may hit the API zone. Watch for it after the change:

```bash
sudo grep -c 'limiting requests' /var/log/nginx/error.log
```

If that climbs, raise `rate=20r/s` in the `ari_api` zone rather than removing the
limit.

---

## 6. Back up the operational state

**Issue #109.** `.user-data/`, `.sessions.json`, `assignments/`, `provenance/`,
`releases/` and `feedback/` are all gitignored and exist only on this host. Lose
the instance and they are gone. `scripts/backfill_id_authors.py` only *partially*
rebuilds provenance — positive rows only, `github:` authors only, and only where
the id is still on file — so the rest is unrecoverable.

`provenance/` is the evidence base for the two-person review rule, so losing it
silently weakens a policy the project depends on.

> **`.sessions.json` holds live GitHub access tokens.** Any backup containing it
> is a **credential** backup. Either exclude it (recommended — a lost session
> store only signs people out) or encrypt the archive and restrict who can read
> it. Do not put it in a git repository, and do not put it anywhere shared.

Create the backup script:

```bash
sudo tee /opt/ari/backup-state.sh >/dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
REPO=/opt/ari/ari-metadata-manager
DEST=/opt/ari/state-backups
STAMP="$(date -u +%Y%m%d_%H%M%S)"
mkdir -p "$DEST"
# .sessions.json is deliberately excluded: it holds live GitHub tokens, and a
# lost session store costs only a re-sign-in.
tar -czf "$DEST/state_$STAMP.tar.gz" -C "$REPO" \
    --exclude=.sessions.json \
    provenance assignments feedback releases .user-data 2>/dev/null
# Keep 30 days.
find "$DEST" -name 'state_*.tar.gz' -mtime +30 -delete
echo "state_$STAMP.tar.gz"
EOF
sudo chmod +x /opt/ari/backup-state.sh
```

Run it once by hand and check the archive is non-empty before scheduling it:

```bash
sudo /opt/ari/backup-state.sh && ls -la /opt/ari/state-backups/
```

Then a daily timer:

```bash
sudo tee /etc/systemd/system/ari-mm-backup.service >/dev/null <<'EOF'
[Unit]
Description=Back up ARI Metadata Manager operational state
[Service]
Type=oneshot
ExecStart=/opt/ari/backup-state.sh
EOF

sudo tee /etc/systemd/system/ari-mm-backup.timer >/dev/null <<'EOF'
[Unit]
Description=Daily ARI Metadata Manager state backup
[Timer]
OnCalendar=daily
Persistent=true
[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now ari-mm-backup.timer
```

**A local archive on the same instance is not a backup.** Copy it off-box —
S3, or a private data branch, following the pattern in
`deploy/update-ontology.sh`. Until that is done, this only protects against
accidental deletion, not against losing the instance.

---

## 7. Ship the logs somewhere queryable

**Issue #122.** Nine modules log sensibly and none of it is aggregated: when a
curator reports "it didn't work", the available evidence is journalctl on one box.

The smallest useful step is to stop losing them to journald's default rotation:

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/ari.conf >/dev/null <<'EOF'
[Journal]
Storage=persistent
SystemMaxUse=500M
MaxRetentionSec=30day
EOF
sudo systemctl restart systemd-journald
```

Then, to read them:

```bash
sudo journalctl -u ari-mm --since "1 hour ago" -p warning
```

Anything better (CloudWatch, Loki, a hosted collector) is a real decision about
cost and where curator data may go, so it is deliberately not scripted here.

---

## 8. Verify the mapping-file migration landed

**Issue #116.** The `mapping-correctness` branch rewrites `mappings/ari.sssom.tsv`
in the repository — that is a normal code change and needs nothing on the server.
After it merges and deploys, confirm the published file validates:

```bash
cd /opt/ari/ari-metadata-manager && python3 scripts/validate_mappings.py
```

It must print `ari.sssom.tsv is valid.` CI runs the same check on every pull
request, so this should never fail; if it does, something merged around the gate.

**Note for curators:** that migration changes one live judgment.
`ARI:0001012 → icd10cm:720.0` was confirmed on 2026-06-25 and flagged on
2026-07-10, with both rows live and nothing marking either as withdrawn. It now
reads as **flagged**, which is what the later judgment said. Tell whoever owns
that mapping.

---

## 9. Restart, and put the timer back

```bash
sudo systemctl restart ari-mm
sudo systemctl start ari-mm-update.timer
```

Confirm it came up:

```bash
curl -sS http://127.0.0.1:8001/healthz
sudo systemctl status ari-mm --no-pager
```

`healthz` should report `"ok": true` and `"diseases": 214`.

---

## What changed about deploys themselves

`deploy/update.sh` now asks `/healthz` before restarting and **defers the restart
when any curator has a working copy live in memory**, retrying on the next
ten-minute tick. So a deploy can take longer to land than it used to. To force
one through for an urgent fix:

```bash
sudo ARI_FORCE_RESTART=1 /opt/ari/ari-metadata-manager/deploy/update.sh
```

Deploying from tags rather than branch tip, and a canary, are still open — see
issue #122.

---

## Rollback

Every change above is reversible:

| Change | To undo |
|---|---|
| `.env` values | Edit `.env`, `sudo systemctl restart ari-mm` |
| nginx config | `sudo cp` the previous file back, `sudo nginx -t`, `sudo systemctl reload nginx` |
| Backup timer | `sudo systemctl disable --now ari-mm-backup.timer` |
| journald config | `sudo rm /etc/systemd/journald.conf.d/ari.conf`, restart journald |
| App code | `git -C /opt/ari/ari-metadata-manager reset --hard <previous sha>`, restart |

The one thing that is **not** reversible from here is a curator revoking and
re-granting the OAuth app: that is their action on GitHub, not yours.
