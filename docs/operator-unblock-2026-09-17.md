# What only the operator can do — 2026-09-17

Five things block the publishing and discoverability work. Each needs an account,
a key, or a decision that is not an agent's to take. They are ordered so that
doing them in sequence is safe; done out of order, item 3 leaks.

Everything else in this area is done and merged: the trusted-publishing workflow,
the version-regression gate, and the denylist-currency gate all landed today and
are green on `main`.

---

## 1. Create PyPI pending publishers — for FOUR packages, not five

<https://pypi.org/manage/account/publishing/> — the **account** sidebar, not a
project sidebar, because these projects do not exist yet.

| Field | Value |
|---|---|
| PyPI Project Name | the name from that package's `pyproject.toml` |
| Owner | `CodeTonight-SA` |
| Repository name | `GRIP` |
| Workflow name | `publish-oss.yml` |
| Environment name | `pypi`, or `testpypi` for the test index |

Do it for `agent-room`, `cc-context-budget`, `false-signal-lint` and `idr-chain`.

**Not `prompt-cache-keepalive`** — see item 2.

A pending publisher does **not** reserve the name. If somebody else registers one
of these first, ours is invalidated, so this is a live race rather than a
formality. On the first successful publish PyPI creates the project and converts
the pending publisher into a normal one, with no further setup.

Then: Actions -> *Publish OSS package* -> pick a package -> `testpypi`. A verify
job runs `./bin/grip-oss-publish --check` first and holds no OIDC permission, so
it cannot upload; only the publish job gets that, and only if the gate passed.

## 2. Decide where `prompt-cache-keepalive` lives

`CodeTonight-SA/prompt-cache-keepalive` is **public at 0.2.0**, with its own CI, a
14.8 KB README, `docs/`, `hooks/` and `scripts/`. `oss/prompt-cache-keepalive` in
this repo is **0.1.0** and a smaller, divergent copy.

Publishing from `oss/` would fix 0.1.0 as that package's first PyPI release,
permanently — a version on PyPI is never replaced, only yanked.
`./bin/grip-oss-publish --check --only prompt-cache-keepalive` now refuses with
exactly that, so nothing can go out by accident.

The decision is which is the home. If it is the public repo, the `oss/` copy
should probably go, and the five-package sibling list belongs in the public
README — which currently names none of the four others, because the file that
travels is that repo's README, not ours.

## 3. The decision chain — in this order, or it leaks

The public front page advertises `decision chain · verifiable` and the badge
**404s**. Everything needed is already in `CodeTonight-SA/grip-decision-chain`:
`index.html`, the 1,981-row chain, and `verify.js` / `verify-anchors.js`, so a
browser verifies the maths with nothing to trust.

Sweeping that chain with the current redaction profile changes **21 of 1,981
rows — 32 replacements, classes `person` and `matter`**. It has not leaked only
because Pages was never enabled.

**a. Bring the publisher current first.** `grip-remote-v2` runs the sync every
fifteen minutes by cron. Its git-crypt is locked, its checkout is 1,185 commits
behind, and its denylist is a plaintext snapshot dated `2026-07-03` with **50**
items against canonical **134** — so 63% of the list is invisible to it. It needs
its git-crypt key, which is why this is yours:

```
cd ~/.claude && git-crypt unlock && git pull --rebase && bin/grip-chain-sync
```

Until that happens the new gate (GRIP#6235) makes it **refuse** rather than
publish stale, so nothing new is accumulating.

**b. Confirm the chain is clean**, after a sync has run with the current list.

**c. Then** make the repo public and enable Pages. Not before — that is the step
that turns those 32 items world-readable.

## 4. Fix the `HAPPI/1.0` badge

The front page advertises `HAPPI/1.0` conformance. `HAPPI.md` is at `happi/1.3`
and `hal-conformance` itself tests 1.3, so the badge undersells the product by
three minor versions. One line in `CodeTonight-SA/.github/profile/README.md`. It
is published brand copy, which is the only reason an agent left it alone.

## 5. Decide whether GRIP itself goes public

`grip-platform/GRIP` is private with 1 star. That is not a blocker for items 1-4
— the five packages publish from a private repo perfectly well, and trusted
publishing needs no visibility change. But it does mean
`pip install git+https://github.com/grip-platform/GRIP...` reaches nobody, so
PyPI was never the only thing standing between these packages and a stranger.

A wholly separate decision from the rest, and the largest.

---

## Front-page claims, tested 2026-09-17

Four of six hold. Recorded here so the audit does not have to be repeated.

| Claim | Verdict |
|---|---|
| Bitcoin anchor "block 956992" | TRUE — block exists, tip was 967468 |
| `RSI witnessed live` badge | TRUE — HTTP 200 |
| `grasp verify .` console block | GROUNDED — documented with `--anchor` tiers |
| `hal-conformance` badge target | TRUE — HTTP 200 |
| `HAPPI/1.0` version | STALE — spec is at 1.3 (item 4) |
| `decision chain · verifiable` | DEAD — HTTP 404 (item 3) |
| "replayable by anyone" | not currently true — the chain is unreachable |

Primary Author: V>>
