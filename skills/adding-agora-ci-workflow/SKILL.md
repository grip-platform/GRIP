---
name: adding-agora-ci-workflow
description: Install the AGORA Council PR-review CI workflow into ANY GitHub repository — copy the canonical workflow, wire the five secrets, pin the GRIP+HAL checkouts, and prove it with a green council run on a real PR. Use when a CodeTonight-SA (or any) repo needs the exogenous 3-speaker + Gemini-judge review gate, after an operator ran AGORA by hand and wants it automated. Covers the public-repo fork-PR secrets caveat and the self-reviewing installer PR (dogfood).
status: Active
effort: high
version: 1.0.0
created: 2026-09-10
triggers:
  - '"add AGORA to this repo"'
  - '"install the AGORA CI workflow"'
  - '"wire up the council gate"'
  - "dogfood-domain test of an AGORA install"
integrates:
  - grip-anywhere
  - absorb-graph
  - absorb-knowledge
  - skill-creation-best-practices
see_also:
  - lib/agora_pr_review.py
  - lib/hal_council_dispatch.py
  - .github/workflows/agora-pr-review.yml
---

# /adding-agora-ci-workflow — the AGORA council gate on any repository

AGORA is GRIP's exogenous PR review: 3 speakers (DeepSeek V4 Flash, Qwen4-Max,
xAI Grok 4.3) write a descriptive 4-section review, Google Gemini 4
Carbon renders the judge verdict. **None of them shares the PR-author model
family.** The workflow that runs it lives in the GRIP repo — this skill is the
recipe for installing it anywhere else.

## When to use

- A repo under CodeTonight-SA (or any org) ships PRs without a council gate.
- An operator just ran `lib/agora_pr_review.py --pr N --repo owner/repo --post`
  by hand and wants that as CI (this is the dogfood path — the installer PR is
  reviewed by the very workflow it installs).

## Pre-flight (run each, never assume)

```bash
# Is the workflow already there?
gh api repos/<owner>/<repo>/contents/.github/workflows/agora-pr-review.yml \
  --jq .name && echo "already installed" || echo "missing — proceed"

# Is GRIP private? Decides whether the checkout needs APPROVE_PAT.
gh api repos/grip-platform/GRIP --jq .private    # true today
gh api repos/grip-platform/HAL --jq .private     # true today

# Pin refs for reproducibility (the recipe embeds them, not `main`)
git ls-remote https://github.com/grip-platform/GRIP.git main | cut -f1
git ls-remote https://github.com/grip-platform/HAL.git main | cut -f1
```

## The recipe

1. **Copy** GRIP's canonical workflow (`.github/workflows/agora-pr-review.yml`)
   into the target repo, then apply the three deltas below.
2. **Delta 1 — checkout GRIP.** The council script is `lib/agora_pr_review.py`,
   which lives in GRIP, not HAL and not the target repo. Add a checkout step:

   ```yaml
   - name: Checkout GRIP (AGORA kernel)
     uses: actions/checkout@v4
     with:
       repository: grip-platform/GRIP
       path: .grip
       ref: <pinned-SHA>            # never a moving branch
       token: ${{ secrets.APPROVE_PAT }}   # REQUIRED: GRIP is private
   ```

3. **Delta 2 — run from `.grip`.** Point the env and the run step at the
   checked-out kernel:

   ```yaml
   env:
     PYTHONPATH: ${{ github.workspace }}/.grip
     HAL_HOME: ${{ github.workspace }}/.hal
   run: |
     export PATH="$HAL_HOME/bin:$PATH"
     cd .grip
     python3 lib/agora_pr_review.py \
       --pr "${{ steps.pr.outputs.number }}" \
       --repo "${{ github.repository }}" \
       --post
   ```

4. **Delta 3 — HAL checkout** stays exactly as in the canonical file
   (`repository: grip-platform/HAL`, `path: .hal`, `token: secrets.APPROVE_PAT`,
   pinned `ref`). HAL is private too.
5. **Secrets** on the target repo (names only — values piped, never printed):

   | Secret | Who | Why |
   |---|---|---|
   | `DEEPSEEK_API_KEY` | DeepSeek V4 Flash | Speaker |
   | `DASHSCOPE_API_KEY` | Alibaba Qwen4-Max | Speaker |
   | `XAI_API_KEY` | xAI Grok 4.3 | Speaker |
   | `GEMINI_API_KEY` | Google Gemini 4 Carbon | Judge (exogenous) |
   | `APPROVE_PAT` | classic PAT, `contents:read` on GRIP + HAL | private checkouts |

   `GH_TOKEN` needs NO secret — the workflow passes `${{ secrets.GITHUB_TOKEN }}`
   (needs `pull-requests: write` + `issues: write`, already in `permissions`).

   ```bash
   # macOS keychain → repo secrets, value never echoed:
   for s in deepseek dashscope xai gemini; do :; done  # per-service names in GRIP
   gh secret set DEEPSEEK_API_KEY -R <owner>/<repo> \
     < <(security find-generic-password -s grip-deepseek -w)
   gh secret set APPROVE_PAT -R <owner>/<repo> \
     < <(security find-generic-password -s grip-gh-pat -w)
   ```

6. **Verify — the dogfood proof.** The PR that installs the workflow IS
   reviewed by the workflow: `pull_request` jobs run from the PR branch's own
   copy of the workflow file, and same-repo branch PRs receive secrets. The
   install is only proven when that run concludes `success` with a real verdict
   (PASS/MERGE or DEFER CHANGES — never a validity refusal or a
   judge-unavailable).

## The fork caveat (public repos — non-negotiable)

GitHub does **not** provide secrets to `pull_request` workflows triggered from
**fork PRs** — the speakers would auth-fail on every fork contribution. The
canonical `if:` gate (`github.repository_owner == '<org>'`) already skips the
rest of GitHub, but for fork PRs the supported path is `workflow_dispatch` with
`pr_number` — a dispatch run has secrets and can review any PR in the repo.
Document this in the repo's CONTRIBUTING, never silently drop the gate.

## Traps

- **`GH_TOKEN` in env must be `secrets.GITHUB_TOKEN`, not `secrets.GH_TOKEN`** —
  a `GH_TOKEN` repo secret does not exist unless you created it; the script
  wants the auto token for comment posting.
- **Do not point `PYTHONPATH` at the target repo** — the target has no
  `lib/agora_pr_review.py`; the import fails with a ModuleNotFoundError that
  looks like a HAL problem.
- **`ref:` pinning.** GRIP's main moves daily; an unpinned checkout silently
  upgrades the kernel under the target repo. Re-pin when the kernel changes
  materially.
- **Validity refusals are not greens.** AGORA fails its own check when a
  speaker cites no diff file or the judge can't render — re-run; never merge on
  a red AGORA check (exogenous-anchor-law).
- **DEFER CHANGES is non-blocking** — merge + surface the deferred items;
  AGORA auto-files them as an issue when labels exist.

## Relationship to the source skills

- **/grip-anywhere** — its stealth doctrine is the consent model: never
  install this workflow into a repo you don't operate. The recipe writes
  nothing upstream beyond the workflow file the operator approves.
- **/absorb-graph** — for org-wide rollout, map which repos lack the gate
  (`lib/org_repo_graph.py`) and order the installs by PR volume, not alphabet.
- **/absorb-knowledge** — after the first green run on a new repo, absorb the
  outcome (repo, pin SHAs, verdict) via
  `lib/hal/knowledge_absorption.py::absorb_from_source` so the next install
  starts from a proven recipe, not this file alone.

## Dogfood anchor (proof this skill works)

Installed on `CodeTonight-SA/CodeTonight-SA.github.io` (public Pages repo) on
2026-09-10: installer PR reviewed by its own workflow run — 3/3 speakers, judge
verdict rendered, run concluded `success`, merged on the green. That run is the
existence proof; anything short of it is untested instructions.
