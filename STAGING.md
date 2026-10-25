# Staging copy at staging.aurint.ca

A second copy of the app on the live Lightsail box, for trying a change end to
end before it reaches `aurint.ca/ari-editor`. It runs any branch of this repo
against any branch of a throwaway copy of the data repo, `KrishnaTO/ARI-staging`,
so its publishes never open a PR on `KrishnaTO/ARI`.

```
Browser ──→ Cloudflare ──→ nginx (server staging.aurint.ca) ──→ uvicorn :8002  ari-mm-staging
                                                                   │
                                     reads and publishes to  KrishnaTO/ARI-staging:<branch>
```

| | Live | Staging |
| --- | --- | --- |
| URL | `https://aurint.ca/ari-editor` | `https://staging.aurint.ca` |
| Checkout, venv | `/opt/ari/ari-metadata-manager`, `/opt/ari/venv` | `/opt/ari-staging/ari-metadata-manager`, `/opt/ari-staging/venv` |
| Port, unit | 8001, `ari-mm` | 8002, `ari-mm-staging` (`MemoryMax=900M`) |
| Data repo | `KrishnaTO/ARI` | `KrishnaTO/ARI-staging` |
| Timers | `ari-mm-*` | `ari-mm-staging-*` |
| ROBOT | `/opt/ari/robot/robot.jar`, shared | same jar |

**Why it is separate in each of these ways:**
- **A subdomain, not a path.** The session cookie is called `session` on both, and on one host signing in to staging would sign you out of the live site.
- **Its own checkout.** All server-side state (`.user-data/`, `provenance/`, `releases/`, …) lives in the checkout, so nothing a curator has on the live site is visible to staging, or touched by it.
- **Its own timers.** The deploy scripts act on the checkout they live in, and `update.sh` restarts `APP_SERVICE` and reads `/healthz` on `PORT`, both from that checkout's `.env`.
- **A memory cap.** The box has 1.9 GB. `MemoryMax` covers ROBOT's JVM too, since it is a child of the service, so when memory runs short the kernel kills staging, not the live app.

**Before you start:** the branch you test must contain the deploy scripts from
the PR that added this file. Merge `main` into it first.

Each step below ends with a check. Run them in order; steps 4 to 9 are on the server.

## 1. The data repo copy

Create an empty **public** repo `KrishnaTO/ARI-staging`. It must be public:
sign-in asks GitHub only for `public_repo` (`app/github_service.py`). It cannot
be a GitHub fork, because an account cannot fork its own repo.

Then push the branches you need into it, from any machine:

```bash
git clone https://github.com/KrishnaTO/ARI.git ari-staging-sync
cd ari-staging-sync
git push https://github.com/KrishnaTO/ARI-staging.git origin/main:refs/heads/main origin/ontology-formatter:refs/heads/ontology-formatter
```

The branches bring their workflows with them, so test PRs get the real checks
(mapping validation, `OWL format`).

**Check:** `https://github.com/KrishnaTO/ARI-staging/branches` lists both.

## 2. A GitHub OAuth app for staging

An OAuth app has one callback URL, so staging needs its own. GitHub → Settings →
Developer settings → OAuth Apps → New OAuth App:
- Homepage URL: `https://staging.aurint.ca`
- Authorization callback URL: `https://staging.aurint.ca/auth/github/callback`

Note the client id and secret for step 5.

## 3. DNS

Cloudflare → DNS → add an **A** record `staging` pointing at the Lightsail static
IP (the same as `aurint.ca`), **Proxied**.

**Check:** `nslookup staging.aurint.ca` answers with Cloudflare addresses.

## 4. Certificate

The origin certificate Cloudflare issues by default covers `aurint.ca` and
`*.aurint.ca`. Confirm the one on the box does:

```bash
openssl x509 -in /etc/ssl/certs/aurint.ca.pem -noout -ext subjectAltName
```

**Check:** it lists `DNS:*.aurint.ca`. If it does not, create a new origin
certificate that does before going on.

## 5. The checkout and its `.env`

```bash
sudo mkdir -p /opt/ari-staging && sudo chown ariapp:ariapp /opt/ari-staging
sudo -u ariapp git clone https://github.com/KrishnaTO/ARI-metadata-manager.git /opt/ari-staging/ari-metadata-manager
cd /opt/ari-staging/ari-metadata-manager && sudo -u ariapp git checkout canonical-owl-format
sudo -u ariapp python3 -m venv /opt/ari-staging/venv
sudo -u ariapp /opt/ari-staging/venv/bin/pip install --no-cache-dir -r requirements.txt
```

**Check that pip finished** before going on:

```bash
ls -l /opt/ari-staging/venv/bin/uvicorn
```

The install builds owlready2 while the live app is running. If memory runs
out, pip stops at `Killed`, and the venv is left without its packages. Step 7
still works, because its scripts need only Python, so the gap shows up only at
step 8 as `Failed to locate executable /opt/ari-staging/venv/bin/uvicorn`. If
`uvicorn` is missing, check `free -m`; with no swap, add it (DEPLOY.md,
*owlready2 pip build is OOM-killed*) and run the install again.

Then the `.env`:

```bash
sudo -u ariapp cp .env.example .env && sudo chmod 600 .env
sudo -u ariapp nano .env
```

Set these in `.env`; leave the rest at the example's values:

```env
GITHUB_CLIENT_ID=<from step 2>
GITHUB_CLIENT_SECRET=<from step 2>
GITHUB_OWNER=KrishnaTO
GITHUB_REPO=ARI-staging
GITHUB_BASE_BRANCH=ontology-formatter
APP_BASE_URL=https://staging.aurint.ca
APP_REPO_BRANCH=canonical-owl-format
APP_SERVICE=ari-mm-staging
PORT=8002
SESSION_SECRET=<new: openssl rand -hex 32, not the live one>
ALLOWED_LOGINS=KrishnaTO
ASSIGN_ADMINS=KrishnaTO
JAVA_TOOL_OPTIONS=-Xmx384m
```

`JAVA_TOOL_OPTIONS` holds ROBOT to a 384 MB heap, which leaves room under
`MemoryMax` for the app itself. If a publish fails with `OutOfMemoryError`,
raise it and `MemoryMax` together, and watch `free -m` while you do.

Two values that are easy to get wrong, because the example ships with the live
site's:
- **`APP_BASE_URL` has no `/ari-editor`.** Staging is served at the root of its
  subdomain, and its nginx site strips no prefix.
- **`OAUTH_CALLBACK_PATH` stays `/auth/github/callback`**, a bare path. The app
  appends it to `APP_BASE_URL` for the OAuth redirect and serves its callback
  page at it, so a full URL here sends GitHub
  `https://staging.aurint.cahttps://staging.aurint.ca/auth/github/callback`,
  which it refuses as "not associated with this application".

**Check:** the branch is the one you meant to test, and the sign-in URLs are
right:

```bash
sudo -u ariapp git -C /opt/ari-staging/ari-metadata-manager log -1 --oneline
sudo grep -E '^(APP_BASE_URL|OAUTH_CALLBACK_PATH)=' /opt/ari-staging/ari-metadata-manager/.env
```

The second prints exactly `APP_BASE_URL=https://staging.aurint.ca` and
`OAUTH_CALLBACK_PATH=/auth/github/callback`, matching the staging OAuth app's
callback URL from step 2.

## 6. Java and ROBOT

Publishing formats the ontology with ROBOT (`app/owl_format.py`, from the
canonical OWL layout PR). This installs what the live site will need at its own
cutover, too; neither touches the live app.

```bash
sudo apt install -y openjdk-17-jre-headless
```

The installer comes from the branch under test, cloned in step 5:

```bash
sudo /opt/ari-staging/ari-metadata-manager/deploy/install-robot.sh
```

**Check:** the installer ends by printing `ROBOT version 1.9.11`.

## 7. First data fetch

```bash
sudo -u ariapp /opt/ari-staging/ari-metadata-manager/deploy/update-ontology.sh
sudo -u ariapp /opt/ari-staging/ari-metadata-manager/deploy/update-mappings.sh
```

**Check:** both report `KrishnaTO/ARI-staging:ontology-formatter`, and
`ls /opt/ari-staging/ari-metadata-manager/ontologies/` shows `ari_t1d.owl`,
`ari_uberon.owl` and `catalog-v001.xml`.

## 8. The app service

```bash
cd /opt/ari-staging/ari-metadata-manager/deploy/staging
sudo cp ari-mm-staging*.service ari-mm-staging*.timer /etc/systemd/system/
echo 'ariapp ALL=(root) NOPASSWD: /bin/systemctl restart ari-mm-staging' | sudo tee /etc/sudoers.d/ari-mm-staging
sudo visudo -cf /etc/sudoers.d/ari-mm-staging
sudo systemctl daemon-reload
sudo systemctl enable --now ari-mm-staging
```

**Check:**
```bash
curl -s http://127.0.0.1:8002/healthz
systemctl status ari-mm-staging --no-pager | grep -E 'Active|Memory'
free -m
```
`/healthz` answers with the branch's commit, and `available` in `free -m` is
still a few hundred MB.

## 9. nginx and timers

This adds a site; the live site's file is not edited. `nginx -t` checks the
whole configuration before the reload, and a failed check leaves the running
nginx as it was.

```bash
sudo cp /opt/ari-staging/ari-metadata-manager/deploy/staging/nginx-staging.conf /etc/nginx/sites-available/ari-mm-staging
sudo ln -s /etc/nginx/sites-available/ari-mm-staging /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo systemctl enable --now ari-mm-staging-update.timer ari-mm-staging-ontology-update.timer ari-mm-staging-mapping-update.timer
```

**Check:** `https://staging.aurint.ca/api/v2/me` shows `"github_enabled": true`,
and `https://aurint.ca/ari-editor` still loads.

## 10. The test publish

1. Open `https://staging.aurint.ca` and sign in.
2. Add one synonym to one disease and publish.
3. The PR opens on `KrishnaTO/ARI-staging` against `ontology-formatter`.

**Check:** the PR's diff is the synonym and its change-log entry, about 5 lines,
and its `OWL format` check passes. Close the PR when done.

## Testing another branch

Edit `APP_REPO_BRANCH` (this repo) and `GITHUB_BASE_BRANCH` (the data copy) in
`/opt/ari-staging/ari-metadata-manager/.env`, push that data branch to the copy
(below), then:

```bash
sudo -u ariapp env ARI_FORCE_RESTART=1 /opt/ari-staging/ari-metadata-manager/deploy/update.sh
sudo -u ariapp /opt/ari-staging/ari-metadata-manager/deploy/update-mappings.sh
```

`update.sh` checks out the new branch, refreshes the ontology and restarts
staging. Without `ARI_FORCE_RESTART=1` it waits while staging has a working copy
in memory, as the live site does. The variable goes through `env` because sudo
can refuse a bare `VAR=value` after `sudo -u ariapp`. The restart itself runs
as `ariapp` through the sudoers rule from step 8; a password prompt here means
that rule is missing or `APP_SERVICE` in `.env` is not exactly `ari-mm-staging`.

## Re-syncing the data copy

The copy does not follow `KrishnaTO/ARI`. To bring one branch up to date:

```bash
cd ari-staging-sync
git fetch origin
git push --force https://github.com/KrishnaTO/ARI-staging.git origin/ontology-formatter:refs/heads/ontology-formatter
```

`--force` replaces the copy's branch with ARI's, so it discards anything merged
into it on the copy. Test PRs still open against it will show ARI's newer
commits in their diffs; close them first.

## Taking it down

```bash
sudo systemctl disable --now ari-mm-staging ari-mm-staging-update.timer ari-mm-staging-ontology-update.timer ari-mm-staging-mapping-update.timer
sudo rm /etc/nginx/sites-enabled/ari-mm-staging && sudo nginx -t && sudo systemctl reload nginx
```

That stops it and frees its memory, and leaves `/opt/ari-staging`, the DNS
record, the OAuth app and `KrishnaTO/ARI-staging` for next time. Delete those
only when you are sure no test is left on them: `/opt/ari-staging/.user-data`
holds any unpublished staging work.
