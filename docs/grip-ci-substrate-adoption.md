# Adopting the GRIP CI Substrate — runbook

**What this gives you.** Any GitHub repository can run GRIP's substrate tools —
the multi-provider HAL router, the deterministic gates, a live non-Anthropic AI
council — inside its own CI, with **zero Claude Code CLI and zero Anthropic
harness**. It is one composite GitHub Action, adopted in a single `uses:` line
per job step. This is the "any-harness / VeriSign-of-AI" moat made portable.

Proven live in three repos as of 2026-07-07: GRIP (self-dogfood, PRs #3874/#3875),
HAL (PR #480 — hypothesis H733 confirmed), and nexus-poc (PR #189).

---

## The two adoption paths

There are two ways to give a repo the substrate. Pick based on how many repos
you expect to onboard.

| Path | Per-repo work | When to use |
|------|---------------|-------------|
| **A — per-repo secrets** | Add one workflow file + set 5 repo secrets | 1–2 repos; works today with no org-admin action |
| **B — org secrets** | Add one workflow file only (secrets inherited) | 3+ repos; the frictionless path, needs a one-time org-admin setup |

Path B is what makes adopter #4 onwards frictionless — the secrets live once at
the organisation level and every private repo's workflow inherits them.

---

## What every adopting workflow needs

Two things reach the composite action, regardless of path:

1. **A token that can read the private `grip-platform/GRIP` repo** (and HAL,
   which the action checks out for the `hal` CLI binary). The adopting repo's
   own built-in `GITHUB_TOKEN` is scoped to *itself* and returns `404 Not Found`
   on GRIP — this is the single most common adoption failure. You must pass a
   token with cross-repo read. In the workflow it is `secrets.GRIP_CHECKOUT_TOKEN`.

2. **Provider API keys** for the live-council job: `DEEPSEEK_API_KEY`,
   `GROQ_API_KEY`, `XAI_API_KEY`, `GEMINI_API_KEY`. These are read by
   `bin/grip-sovereign-council` through GRIP's env-first key ladder
   (`lib/provider_secret.py`, GRIP PR #3878) — never a macOS keychain, so they
   work unchanged in Linux CI. The deterministic-proof job needs none of these.

---

## Path A — per-repo secrets (works today)

From a shell with `gh` logged in as an org member who can write the repo's
secrets:

```bash
REPO=CodeTonight-SA/your-repo

# 1. Cross-repo checkout token (a token that can READ GRIP + HAL).
#    Simplest: your own gh token. Better: a dedicated fine-grained PAT (below).
gh auth token | gh secret set GRIP_CHECKOUT_TOKEN -R "$REPO"

# 2. Provider keys (piped from the local keychain; the value never prints).
security find-generic-password -s grip-deepseek -w | gh secret set DEEPSEEK_API_KEY -R "$REPO"
security find-generic-password -s grip-groq     -w | gh secret set GROQ_API_KEY     -R "$REPO"
security find-generic-password -s grip-xai      -w | gh secret set XAI_API_KEY      -R "$REPO"
security find-generic-password -s grip-gemini   -w | gh secret set GEMINI_API_KEY   -R "$REPO"
```

Then add the workflow file (template below) and open a PR.

---

## Path B — org-level secrets (frictionless for many repos)

Set the five secrets **once** at the `CodeTonight-SA` organisation level, scoped
to private repositories. Every adopting private repo then inherits them and each
new adoption is just the workflow file — no secret step.

**Prerequisite (one-time):** the `gh` token needs the `admin:org` scope, which
the default `repo,workflow,read:org` login does not carry. Refresh it:

```bash
gh auth refresh -h github.com -s admin:org   # opens a browser; approve once
```

**Recommended for `GRIP_CHECKOUT_TOKEN`: mint a dedicated fine-grained PAT**
rather than spraying a personal OAuth token org-wide. A personal token carries
your entire account's reach into every repo that can read the secret; a
purpose-built token is least-privilege and independently revocable.

- GitHub → Settings → Developer settings → Fine-grained tokens → Generate.
- Resource owner: `CodeTonight-SA`. Repository access: only `GRIP` and `HAL`.
- Permissions: Repository → **Contents: Read-only**. Nothing else.
- Copy the `github_pat_…` value into the command below instead of `gh auth token`.

**Set the org secrets** (visibility `private` excludes public repos like the
GRASP mirror; use `--repos a,b,c` to restrict further):

```bash
ORG=CodeTonight-SA

# checkout token — paste the fine-grained PAT value when prompted
gh secret set GRIP_CHECKOUT_TOKEN --org "$ORG" --visibility private

# provider keys
security find-generic-password -s grip-deepseek -w | gh secret set DEEPSEEK_API_KEY --org "$ORG" --visibility private
security find-generic-password -s grip-groq     -w | gh secret set GROQ_API_KEY     --org "$ORG" --visibility private
security find-generic-password -s grip-xai      -w | gh secret set XAI_API_KEY      --org "$ORG" --visibility private
security find-generic-password -s grip-gemini   -w | gh secret set GEMINI_API_KEY   --org "$ORG" --visibility private

gh api orgs/$ORG/actions/secrets --jq '[.secrets[].name]'   # verify
```

**Security note.** Org secrets with `private` visibility are readable by the
Actions of *every* current and future private repo in the org. That is the
convenience and the risk: a malicious workflow in any such repo could exfiltrate
them. Mitigations: use least-privilege fine-grained tokens, scope with `--repos`
where practical, and rotate the provider keys on the normal cadence. This is a
deliberate, reversible posture choice — set it consciously, not by default.

---

## The workflow file (copy verbatim, adjust the two names)

Save as `.github/workflows/grip-ci-substrate.yml` in the adopting repo. Change
the job `name:` strings to your repo; everything else is portable.

```yaml
name: Sovereign CI Substrate (GRIP tools, zero claude-code)
on:
  workflow_dispatch:
  pull_request:
    paths:
      - '.github/workflows/grip-ci-substrate.yml'
permissions:
  contents: read
jobs:
  deterministic-proof:
    if: github.repository_owner == 'CodeTonight-SA'
    name: "GRIP substrate runs in CI (no claude-code)"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          repository: grip-platform/GRIP
          token: ${{ secrets.GRIP_CHECKOUT_TOKEN }}
          submodules: false
          fetch-depth: 1
      - uses: grip-platform/GRIP/.github/actions/grip-ci-substrate@main
        with:
          hal-token: ${{ secrets.GRIP_CHECKOUT_TOKEN }}
          command: >-
            python3 -c "import lib.hal_llm_adapter, lib.redaction_shield, lib.idr_forest;
            print('PROOF: GRIP substrate runs in CI — zero Claude Code CLI, zero Anthropic harness')"

  sovereign-council:
    if: github.repository_owner == 'CodeTonight-SA'
    name: "Live non-Anthropic council in CI"
    runs-on: ubuntu-latest
    env:
      DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
      GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
      XAI_API_KEY: ${{ secrets.XAI_API_KEY }}
      GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
    steps:
      - uses: actions/checkout@v4
        with:
          repository: grip-platform/GRIP
          token: ${{ secrets.GRIP_CHECKOUT_TOKEN }}
          submodules: false
          fetch-depth: 1
      - uses: grip-platform/GRIP/.github/actions/grip-ci-substrate@main
        with:
          hal-token: ${{ secrets.GRIP_CHECKOUT_TOKEN }}
          command: 'bin/grip-sovereign-council --speakers deepseek,groq,xai --judge gemini-pro "One sentence: what does a tamper-evident decision record prove?"'
```

Notes baked in from three live adoptions:

- **`hal-token` uses `GRIP_CHECKOUT_TOKEN`, not `github.token`.** `github.token`
  works only when the action is checking out its *own* repo. In any repo that is
  not HAL, the built-in token cannot read the private HAL repo — pass the
  cross-repo token to `hal-token` too.
- **`submodules: false`** — never recurse GRIP/HAL submodules; a broken gitlink
  otherwise fails the checkout.
- **The composite action exports `PATH`/`HAL_HOME`/`PYTHONPATH`** against the
  checked-out GRIP tree itself, so callers add no `cd`, `working-directory`, or
  `PYTHONPATH` steps. A reviewer flagging "missing PYTHONPATH" is reading the
  caller, not the action.
- **`paths:` filter** keeps this off regular PRs — it is an on-demand +
  self-test workflow, not a per-PR reviewer.

---

## The load-bearing security guardrail (never skip)

GRIP is git-crypt encrypted. **Never put the git-crypt key in CI.** That would
leak the entire encrypted client-data corpus through one CI secret. The substrate
works only because the tools it runs — and their full dependency closure — are in
the git-crypt **de-crypted** set (`.gitattributes` leaves non-sensitive
operational code readable). Before adding any `command:` that runs a GRIP tool,
verify every path it reads is de-crypted:

```bash
git check-attr filter -- <path>     # "filter: unspecified" = de-crypted, CI-safe
                                    # "filter: git-crypt"   = encrypted, CI-FORBIDDEN
```

If a tool reads an encrypted file (`rules/operational-context.md`, genomes,
sensitive configs) it cannot run in CI without the key — so it does not run in CI.

---

## Verify an adoption (verify-canonical, not "it should work")

```bash
PR=<number>; REPO=CodeTonight-SA/your-repo
gh pr checks "$PR" --repo "$REPO"                    # both substrate jobs green
RUN=$(gh run list -R "$REPO" --workflow "Sovereign CI Substrate (GRIP tools, zero claude-code)" --limit 1 --json databaseId --jq '.[0].databaseId')
gh run view "$RUN" -R "$REPO" --log | grep -E "PROOF|Anthropic calls|PROVENANCE"
```

A genuine adoption prints the `PROOF` line, `Anthropic calls : 0`, and a
`PROVENANCE sha256` seal from the council job.

---

## Known adoption failure modes (all three hit at least once)

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Could not find file … skills/_archived/…` at action staging | A **dangling symlink** in GRIP (target untracked). The runner stages the whole action repo and refuses. | Fixed upstream in GRIP; if it recurs, every `git ls-tree` mode-120000 target must be a tracked path. |
| `Not Found - get-a-repository` at the checkout step | Checkout token cannot read GRIP (e.g. a repo-scoped `APPROVE_PAT`). | Use `GRIP_CHECKOUT_TOKEN` with cross-repo read. |
| Council job: missing-credential / empty key | Provider keys absent or under wrong names. | Set the four `*_API_KEY` secrets; they reach the action via job-level `env:`. |
| Both jobs silently skipped | `repository_owner` guard on a fork. | Expected — forks have no secrets; the guard keeps them quiet. |

---

Siblings: `.github/actions/grip-ci-substrate/action.yml` (the action),
`.github/workflows/grip-ci-substrate-dogfood.yml` (GRIP's self-test),
`rules/pool-a-offload-routing.md` (why non-Anthropic CI matters for the weekly
limit), `bin/grip-harness` (the same substrate wired into local foreign harnesses).
