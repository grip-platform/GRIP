# GRIP Deploy URLs

Central reference for all GRIP deployment endpoints. Keep this file updated when URLs change.

## Production Deployments

### Frontend: Copilot Dashboard (Vercel)

| Property | Value |
|----------|-------|
| URL | `https://grip-copilot.vercel.app` |
| Source | `apps/grip-web/` |
| Config | `apps/grip-web/vercel.json` |
| Deploy path | Vercel's own Git integration. There is no CI workflow -- this table used to name `deploy-frontend.yml`, deleted in `c82a32a0` (2026-03-14, "remove Vercel deployment workflows to reduce build costs"). |
| Trigger | Push to `main` **that changes `apps/grip-web/`**. Other commits are skipped by the `ignoreCommand` in the config. |
| Previews | Off for every branch whose name contains a `/`, which is every branch our tooling creates. Suppression pattern: `"*/**/*": false` under `git.deploymentEnabled`. A hand-made slashless branch (`hotfix`) is not matched and would still build one. |

### Frontend: GRIP Landing Page (Vercel)

| Property | Value |
|----------|-------|
| URL | `https://grip-mauve.vercel.app` |
| Source | `site/` |
| Config | `site/vercel.json` |
| Deploy path | Vercel's own Git integration. No CI workflow. |
| Trigger | Push to `main` **that changes `site/`**. Other commits are skipped by the `ignoreCommand` in the config. |
| Previews | Off for every branch whose name contains a `/`, which is every branch our tooling creates. Suppression pattern: `"*/**/*": false` under `git.deploymentEnabled`. A hand-made slashless branch (`hotfix`) is not matched and would still build one. |
| Notes | Static site (Vite). Contains whitepaper and project overview. Also serves `your-gateway.example.com` via a host redirect. |

### Why both sites skip most commits

GRIP merges to `main` many times a day, and Vercel rebuilds a project on every
commit to its production branch by default. Almost none of those commits touch a
website: measured over 2026-08-11 to 2026-08-25, 242 of 246 commits on `main`
changed neither `apps/grip-web/` nor `site/`, yet each one triggered two builds.

Both `vercel.json` files therefore carry:

```json
"ignoreCommand": "git diff HEAD^ HEAD --quiet -- ."
```

Vercel runs that from the project's Root Directory and inverts the intuitive
reading of the exit code: **0 skips the build, 1 builds it**. `git diff --quiet`
exits 0 when nothing changed, so a commit that leaves the site untouched is
skipped and one that changes it deploys as before. If `HEAD^` is unavailable the
command errors non-zero, which Vercel reads as "build" -- the degraded case
deploys rather than silently freezing the site.

The `.` pathspec is complete because both builds are self-contained: the repo
root has no `package.json`, workspace manifest or root lockfile, and each
project carries its own `package.json` + `package-lock.json` inside its own
directory with no source imports from outside it. A root-level file therefore
cannot influence either build, so no root path needs to be added to the
`ignoreCommand`. (Re-checked 2026-08-30 for AGORA #5085 round 2; guarded by
`test_build_inputs_do_not_escape_the_root_dir`.)

### Preview deployments for slashed branches are gone — on purpose

Every branch GRIP's tooling creates contains a `/` (`feat/x`, `fix/y`,
`dependabot/npm/z`), and the `git.deploymentEnabled` rule
`"*/**/*": false` suppresses previews for all of them. Two `/`-bearing
separators are deliberate: `main` carries none, so it can never match the
suppression pattern (PR #4631 showed a `**` rule CAN match `main`, which froze
production for 14 minutes). The price is that a push to a feature branch no
longer produces a shareable preview URL.

When a preview is genuinely needed, either:

1. **Run it locally** — `npm install && npm run dev` inside the project
   directory serves the exact same Vite app on `localhost`.
2. **Temporary slashless branch** — push the work to a hand-made branch name
   with no `/` (e.g. `preview-thing-123`) and Vercel builds one preview;
   delete the branch afterwards. Note this also produces a preview for the
   OTHER site, since the rule is per-project and matches any slashless
   non-`main` branch.
3. `npx vercel dev` inside the project directory for a Vercel-routed local
   preview.

Anchored by `tests/test_vercel_build_scoping.py`.

### Backend: Copilot API (Railway)

| Property | Value |
|----------|-------|
| URL | `https://grip-copilot-production.up.railway.app` |
| Health | `https://grip-copilot-production.up.railway.app/api/copilot/health` |
| Source | `apps/copilot-api/` |
| Config | `apps/copilot-api/railway.json` |
| CI Workflow | `.github/workflows/deploy-backend.yml` |
| Trigger | Push to `main` affecting `apps/copilot-api/**` |
| Runtime | Uvicorn (FastAPI), port from `$PORT` env var |
| Env Vars | `RAILWAY_TOKEN` (GitHub Secret) |

### CORS Configuration

The Copilot API allows requests from:
- `https://grip-copilot.vercel.app`
- `https://grip-copilot-*.vercel.app` (preview deployments)

Defined in `apps/copilot-api/main.py` and `scripts/copilot-api.py`.

## Infrastructure (Live)

<!-- Was "Planned, Not Yet Deployed" until 2026-08-31. The box is deployed and
     serving public traffic - see docs/HETZNER-VPS-GUIDE.md. This block survived a
     substantial rewrite of this same file two days earlier, because a status table
     describes STATE and nothing re-reads it when the state moves. -->

### Hetzner VPS

| Property | Value |
|----------|-------|
| Status | LIVE - see `docs/HETZNER-VPS-GUIDE.md` |
| Plan | Hetzner CX22 (2 vCPU, 4 GB RAM, 40 GB NVMe) |
| Access | Tailscale VPN only (zero public ports) |
| Documentation | `docs/HETZNER-DEPLOYMENT-PLAN.md` |

Services planned for VPS:
- `grip-mcp-web` (Docker, port 3000 via Tailscale) — formerly `grip-mcp-server`
- `metrics-server.py` (port 9100 via Tailscale)
- `session-control-server.py` (ports 19000-19999 via Tailscale)
- `copilot-scheduler.py` (systemd timer, local only)

## Local Services

### Copilot Scheduler (launchd)

| Property | Value |
|----------|-------|
| Config | `scripts/copilot-scheduler.plist` |
| Dashboard env | `GRIP_DASHBOARD_URL=https://grip-copilot.vercel.app` |
| Schedule | Every 15 minutes |

## Environment Variables

| Variable | Where Used | Value |
|----------|-----------|-------|
| `GRIP_DASHBOARD_URL` | `lib/copilot/workflow_runner.py`, scheduler plist | `https://grip-copilot.vercel.app` |
| `VERCEL_TOKEN` | Unused | Legacy of the deleted `deploy-frontend.yml`; no workflow reads it. |
| `VERCEL_ORG_ID` | Unused | Legacy of the deleted `deploy-frontend.yml`; no workflow reads it. |
| `VERCEL_PROJECT_ID_WEB` | Unused | Legacy of the deleted `deploy-frontend.yml`; no workflow reads it. |
| `RAILWAY_TOKEN` | GitHub Actions | Secret |

## Security

- All deployment tokens stored as GitHub Secrets (never in code)
- Railway health check has exponential backoff (4 attempts, 15s base delay)
- The two Vercel sites deploy through Vercel's Git integration, not a CLI or a
  GitHub Action. The `--prod --yes` CLI invocation described here previously
  belonged to `deploy-frontend.yml`, deleted in `c82a32a0` (2026-03-14).
- See `docs/deployment-security.md` for the 5-rule security gate
- See `docs/HETZNER-DEPLOYMENT-PLAN.md` for VPS threat model

## Related Documentation

- `docs/deployment-security.md` -- 5-rule deployment security gate
- `docs/HETZNER-DEPLOYMENT-PLAN.md` -- VPS architecture and setup
- `docs/COPILOT-ARCHITECTURE.md` -- Copilot system design
