---
name: activating-dwd-meet-absorb
description: Zero-friction step-by-step click-path for activating the domain-wide delegation (DWD) Meet absorb pipeline in GCP Console + admin.google.com. Use when V>> or any Workspace admin is ready to complete the one-time browser setup that unblocks org-wide Gemini Notes absorb for every CodeTonight user without per-user OAuth. Companion to docs/DWD-MEET-ABSORB.md and lib/meet_absorb_dwd.py.
---

# Activating DWD Meet Absorb

**Audience**: Workspace super admin (`licensing@codetonight.co.za` or `development@codetonight.co.za`).
**Time**: ~10 minutes.
**Prerequisites**: Google Workspace super admin role, access to <https://console.cloud.google.com/> and <https://admin.google.com/>.
**Post-state**: `scripts/meet-absorb.sh --dwd --user <development@codetonight.co.za> --latest` works for every domain user.

## Pre-flight (1 minute)

Run locally before opening a browser:

```bash
# Confirm the DWD scaffold is present on main
cd ~/.claude
git log --oneline --grep=DWD -5

# Confirm the Python path + venv has google-api-python-client
~/.claude/venv/bin/python -c "from google.oauth2 import service_account; print('ok')" 2>&1 || \
    ~/.claude/venv/bin/pip install google-api-python-client google-auth tenacity

# Confirm .secrets/ is gitignored (the key will live here)
grep -n "^\.secrets" ~/.claude/.gitignore || echo "WARNING: .secrets not gitignored"

# Confirm the target directory exists
mkdir -p ~/.claude/.secrets && chmod 700 ~/.claude/.secrets
```

If any of those fail, stop and fix before proceeding — the GCP steps assume a working local scaffold.

## Step 1 — GCP Console (5 minutes)

Open <https://console.cloud.google.com/>. Sign in as `licensing@codetonight.co.za`.

### 1.1 Create or select a project

- Top bar → project selector → **New Project**
- Name: `grip-meet-absorb`
- Organization: `codetonight.co.za`
- Click **Create**. Wait ~5s for the project to appear.
- Switch the top bar to the new project.

### 1.2 Enable the APIs (15 total for ALL SCOPES mode)

Navigate to **APIs & Services → Library**. For each API below, search by name, click the result, and click **Enable**. You can leave each tab open and come back:

1. Google Drive API
2. Google Docs API
3. Google Sheets API
4. Google Slides API
5. Google Forms API
6. Google Calendar API
7. Gmail API
8. **Admin SDK API** (critical — required for `--auto` dynamic user enumeration)
9. Google Chat API
10. Google Tasks API
11. Google People API
12. Google Contacts API (may be bundled with People)
13. Google Keep API
14. Google Meet REST API
15. Google Classroom API

**Tip**: use the "Enable APIs and services" button at the top of the Library page and paste each name to skip the search overhead.

### 1.3 Create the service account

- Left nav → **IAM & Admin → Service Accounts**
- **+ Create Service Account**
- Name: `grip-meet-absorb-sa`
- Description: `Domain-wide delegation service account for GRIP Meet absorb pipeline`
- **Create and continue**
- **Grant this service account access to project**: leave blank (DWD does not need project-level roles). Click **Continue**.
- **Grant users access**: leave blank. Click **Done**.

### 1.4 Enable domain-wide delegation on the service account

- Click the newly-created `grip-meet-absorb-sa` row
- Tab: **Details**
- Expand **Advanced settings**
- Under **Domain-wide delegation**, click **View Google Workspace Admin Console** to confirm you have admin access (opens admin.google.com in a new tab — leave it for Step 2).
- Back on GCP: tick **Enable Google Workspace domain-wide delegation**
- **OAuth consent screen product name**: `GRIP Meet Absorb` (required, shown to users in their audit logs)
- **Save**

### 1.5 Copy the Client ID

- Still on the service account Details tab, find **Unique ID** or **OAuth 2 Client ID** (a long numeric string, ~21 digits).
- Copy it to your clipboard. You'll paste it in Step 2.

### 1.6 Create and download the JSON key

- Tab: **Keys**
- **Add key → Create new key → JSON → Create**
- The browser downloads a file like `grip-meet-absorb-XXXXXXXX.json`.
- Move it to `~/.claude/.secrets/dwd-key.json`:

```bash
mv ~/Downloads/grip-meet-absorb-*.json ~/.claude/.secrets/dwd-key.json
chmod 600 ~/.claude/.secrets/dwd-key.json
```

**NEVER commit this file.** The `.gitignore` already excludes `.secrets/`, but double-check:

```bash
git status --ignored | grep dwd-key.json
# Should appear under "Ignored files"
```

## Step 2 — admin.google.com (3 minutes)

Open <https://admin.google.com/>. Sign in as `licensing@codetonight.co.za` (super admin).

### 2.1 Navigate to Domain-wide delegation

- Left nav → **Security**
- **Access and data control → API controls**
- Panel at the bottom: **Domain-wide delegation → Manage Domain Wide Delegation**

### 2.2 Add the new delegation

- Click **Add new**
- **Client ID**: paste the numeric ID from Step 1.5
- **OAuth scopes**: paste the full comma-separated scope list. The canonical list lives in `lib/meet_absorb_dwd.py` as the `SCOPES` tuple — export it with:

```bash
~/.claude/venv/bin/python -c "
import sys
sys.path.insert(0, str(__import__('pathlib').Path.home() / '.claude'))
from lib.meet_absorb_dwd import SCOPES
print(','.join(SCOPES))
" | pbcopy
echo "Scopes copied to clipboard — paste into the admin.google.com scope field"
```

The clipboard now has 34 comma-separated scopes ready to paste.

- Click **Authorize**

Delegation propagates in ~2-5 minutes. You can close the admin.google.com tab.

## Step 3 — Verification (2 minutes)

Back on the local machine:

```bash
cd ~/.claude

# Smoke test: enumerate the domain via Admin SDK
~/.claude/venv/bin/python scripts/meet-absorb-dwd.py --auto --admin-user licensing@codetonight.co.za
# Expected: "Enumerated N active domain user(s) via Admin SDK"
# Where N matches the number of active @codetonight.co.za users

# Smoke test: list Gemini docs for a specific user (no absorb)
~/.claude/venv/bin/python scripts/meet-absorb-dwd.py --list --user development@codetonight.co.za
# Expected: list of Gemini Notes docs owned by Andre

# End-to-end test: absorb Andre's latest Gemini doc
~/.claude/venv/bin/python scripts/meet-absorb-dwd.py --latest --user development@codetonight.co.za
# Expected: "Saved: .../africus-*.txt (N chars) [owner=development@codetonight.co.za]"
# If absorbed before, you'll see "Already processed:" instead — expected

# Run the test suite to confirm nothing broke
~/.claude/venv/bin/python -m pytest tests/test_meet_absorb_dwd.py -q
# Expected: 30 passed
```

If all four smoke tests pass, DWD is live.

## Troubleshooting

### `google.auth.exceptions.RefreshError: unauthorized_client`

Delegation hasn't propagated yet (allow 5 min after Step 2) OR one of the scopes in admin.google.com doesn't match the scopes the code requests. Re-copy from `lib/meet_absorb_dwd.SCOPES` and paste again in admin.google.com.

### `HttpError 403: Not Authorized to access this resource/api`

The specific API wasn't enabled in Step 1.2. Enable it in GCP Console → APIs & Services → Library.

### `HttpError 403: Admin SDK API has not been used in project...`

Step 1.2 missed the Admin SDK API specifically. Enable it and retry the `--auto` smoke test.

### `FileNotFoundError: DWD key missing: ~/.claude/.secrets/dwd-key.json`

Step 1.6 didn't complete. Re-download the JSON key and place it at the exact path.

### Admin Reports scopes rejected

`admin.reports.audit.readonly` and `admin.reports.usage.readonly` require the impersonating admin user (`laurie@`) to hold the **Admin Auditor** role in admin.google.com (Account → Admin roles → Admin Auditor → assign). If V>> doesn't need audit reports, drop those two scopes from `lib/meet_absorb_dwd.py:SCOPES`, rerun Step 2.2 with the shorter list, and re-verify.

### `--auto` returns sparse `403 Not Authorized to access this resource/api` from Admin SDK

**Root cause**: the impersonated Workspace user lacks the `roles/serviceusage.serviceUsageConsumer` IAM binding on the SA's Cloud project (e.g. `your-gcp-project-id`). Admin SDK enforces `serviceusage.services.use` on the quota project more strictly than Drive or Calendar, so DWD impersonation silently fails at the API routing layer even though the token carries all 33 scopes and the DWD row is correctly configured.

**Diagnostic signature**: every Admin SDK Directory endpoint (`users.list`, `users.get`, `domains.list`, `groups.list`) returns the sparse `"Not Authorized to access this resource/api"` error shape, while `customers.get` returns `"Insufficient Permission"` (a *different* scope-class error). Drive + Calendar + Gmail impersonation continue working throughout.

**Workaround (RECOMMENDED)**: bypass Admin SDK entirely with a static users file.

```bash
cat > ~/.claude/data/workspace-users.txt <<'EOF'
# One email per line, # for comments
licensing@codetonight.co.za
development@codetonight.co.za
development@codetonight.co.za
development@codetonight.co.za
EOF

# --auto now reads from this file automatically
meet-absorb-dwd.py --auto
```

`meet-absorb-dwd.py` auto-detects `data/workspace-users.txt` and prefers it over Admin SDK. Pass `--users-file PATH` explicitly for a non-default location. Maintenance: update the file when team membership changes.

**Proper fix (if you need dynamic enumeration)**: grant `roles/serviceusage.serviceUsageConsumer` on the SA's Cloud project to every Workspace user who will be impersonated:

```bash
for user in laurie andre arnold; do
  gcloud projects add-iam-policy-binding your-gcp-project-id \
    --member="user:${user}@codetonight.co.za" \
    --role="roles/serviceusage.serviceUsageConsumer"
done
```

This is a customer-wide IAM change — evaluate the blast radius before running it.

## Rollback

To disable DWD without losing the scaffold:

```bash
# 1. Remove the key locally
rm -f ~/.claude/.secrets/dwd-key.json

# 2. admin.google.com → Security → Domain-wide delegation → remove row
# 3. GCP Console → service account → Delete key (not the SA itself) or delete the SA
```

The legacy `gog`-based `scripts/meet-absorb.sh` path remains functional throughout.

## Success signals

- `scripts/meet-absorb-dwd.py --auto` enumerates every active domain user in real time.
- Any CodeTonight meeting's Gemini Notes doc can be absorbed regardless of organiser, without Andre having to manually share.
- Post-meeting auto-share (attendee-only) still works as a fallback.
- Hypothesis H021 (`coverage_pct ≥ 99` by 2026-04-17) enters the measurement window.

## Related

- `docs/DWD-MEET-ABSORB.md` — architecture and security rationale
- `lib/meet_absorb_dwd.py` — core library with `SCOPES`, `list_gemini_docs`, `list_domain_users`
- `scripts/meet-absorb-dwd.py` — CLI wrapper
- `tests/test_meet_absorb_dwd.py` — 30 tests, including impersonation invariant
- `scripts/meet-absorb.sh --dwd` — backwards-compatible passthrough to the Python CLI
