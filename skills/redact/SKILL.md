---
name: redact
description: Canonical redaction / PII shield — redact client names, personal identities, emails, phones, IPs, JIDs, hosts and key shapes from ANY text before it reaches a public surface or a cloud LLM, with pinned pseudonyms so the context still knows what it is talking about. Use when the operator says "redact", "anonymise", "PII shield", "sanitise for the LLM", "scrub client names", before publishing any text artifact (decision chain, reports, posts), or before sending client-context prompts to an external provider. Wraps lib/redaction_shield.py (L1 deterministic floor + optional L2 LLM recall).
---

# /redact — Canonical Redaction & PII Shield

ONE shield for every surface that carries free text toward an audience that
must not learn client identities. Third instance of the "decorative
real-data" leak class earned this shim (N=3, knowledge-maturation-functor):
rendered video frames (2026-06-17) → the public decision chain (2026-07-02)
→ un-shielded LLM egress. Core: `lib/redaction_shield.py`. Denylist:
`config/redaction-denylist.json` (git-crypt encrypted — the list itself
names the identities it protects; NEVER quote its entries in plaintext
output, commits, or PR bodies).

## The two profiles

| Profile | What it does | Use for |
|---------|-------------|---------|
| `public` (default) | Denylist entities → PINNED pseudonyms (`[ORG-1]`, `[PERSON-1]`) + generic PII floor (email/phone/IP/JID/key → opaque tokens). Deterministic: same text in ⇒ same text out (Banach publish contract). | Anything published: decision chain, reports, posts, docs, screenshots of text. |
| `llm_egress` | Everything `public` does PLUS pattern discovery of unlisted PERSON/ORG/CASE shapes and a session mapping — the LLM sees `[PERSON-8]` used consistently ("the context still knows what we are talking about") and `deanonymize()` restores originals locally. | Prompts leaving for a cloud provider with client context. |

## Usage

```bash
# Redact a file or stdin (public profile)
venv/bin/python -m lib.redaction_shield redact drafts/post.md
echo "Met Jane Example at the client" | venv/bin/python -m lib.redaction_shield redact -

# Detector mode — exit 2 + findings on stderr if ANYTHING would be redacted.
# Use as a pre-publish gate on any text artifact:
venv/bin/python -m lib.redaction_shield verify exports/report.md

# Strict gate — exit 2 on ANY residue: what redact() would replace PLUS, under
# the public profile, the name-shaped spans blockers() would hold a publish
# for. This is the gate before an external model call; plain verify is not.
venv/bin/python -m lib.redaction_shield verify --strict --profile llm_egress prompt.md

# Several lists — a matter's own denylist in front of the repo list. --config
# is repeatable; lists merge in the order given and at equal alias length the
# FIRST list's pin wins, so the matter's role pins ([MOTHER-1]) beat the repo's
# generic ones ([PERSON-13]) for the same person.
venv/bin/python -m lib.redaction_shield verify --strict --profile llm_egress \
    --config <matter>/work/denylist.json --config config/redaction-denylist.json -

# Prove the fixed point (idempotence) — redact(redact(x)) == redact(x)
venv/bin/python -m lib.redaction_shield selftest

# Remediate the public decision-chain ledger in place (chain-safe)
venv/bin/python -m lib.idr_public_emitter remediate
```

```python
from redaction_shield import Shield, ShieldSession, load_denylist, llm_scan

shield = Shield(load_denylist())                       # public profile
clean = shield.redact(text).text

session = ShieldSession()                              # llm_egress profile
egress = Shield(load_denylist(), profile="llm_egress", session=session)
prompt_out = egress.safe(prompt).text                  # redact, then RE-READ the output;
                                                       # raises ShieldResidue if anything survived
reply = session.deanonymize(llm_reply)                 # CANONICALS restored locally

matter_first = load_denylists([matter_json, DEFAULT_CONFIG])   # merged, matter pins win ties
egress = Shield(matter_first, profile="llm_egress", session=ShieldSession())
prompt_out = egress.safe(prompt).text                  # the merged list, end to end
```

## Sufficient on its own (2026-10-01)

Measured against a legal matter's own pseudonymiser on fake data, the shield
alone was NOT sufficient before an external model call and the two had to be
composed, pseudonymiser first. Eight defects were fixed, one test each in
`tests/test_redaction_shield_sufficiency.py`: `canonical` is now compiled into
the matcher when no alias spells it; aliases are fenced by `(?<!\w)`/`(?!\w)`
instead of `\b`, so one ending in `)` or `.` can match; every alias and every
text is NFC-normalised; the floor carries a 13-digit identity-number shape
(contiguous or spaced, date-validated so a millisecond epoch is kept) and the
local `0xx xxx xxxx` phone shape; the allowlist passes a candidate only when
the WHOLE candidate or EVERY word of it is listed (one single-word brand no
longer disarms "Jane Grip"); `--config` is repeatable; `verify --strict` and
`verify(text, strict=True)` refuse on any residue; `deanonymize()` restores
the canonical. `safe()` is the pseudonymiser's discipline absorbed: it re-reads
its own output. The composition is no longer required; where a matter keeps
its own list, pass it FIRST with `--config`.

## Layering (llm-augmentation-pattern compliant)

- **L1 (this shield)** — deterministic floor: denylist + generic PII regex,
  single-alternation single-pass, longest-span-wins. Always available.
- **L2 (`llm_scan`)** — optional semantic recall over ALREADY-L1-shielded
  text via GRIP's canonical AI syscall. Detector only, monotone (can only
  ADD findings), fail-open unless `strict=True`. Findings feed the denylist
  — L2 never rewrites, so deterministic pipelines stay deterministic.

## Where it is wired (mechanical, not optional)

- **Emit-time**: `lib/idr_public_emitter.py` shields every public free-text
  field (subject/what/why/how) as rows are created.
- **Publish gate**: `subjects_with_leaks` reports denylist findings;
  `decision_chain_publish.live_publish` REFUSES on a git-crypt-locked
  denylist (fail closed — a checkout that cannot read the list cannot know
  the chain is clean).
- **Remediation**: `idr_public_emitter remediate` rewrites an existing
  ledger through the shield in place — chain-safe (sha/prev_sha untouched)
  and idempotent (second run changes 0 rows).

## Denylist maintenance

Add entities to `config/redaction-denylist.json` (encrypted at rest):
`{"canonical": "...", "aliases": ["...", {"alias": "Bob", "cs": true}],
"pseudonym": "[PERSON-9]", "class": "person"}`. Pin the pseudonym index —
determinism depends on it. Short/ambiguous tokens get `"cs": true`. The
canonical is matched case-insensitively even when no alias spells it; list
it as a `"cs": true` alias if it needs case sensitivity.
Lesson from the live remediation: include SLUG and USERNAME forms
(`jane-example-dev`, `daily-to-jane`) -- word boundaries do not reach
inside concatenated identifiers. The allowlist holds public brands that
must never be redacted.

## Boundaries

- The denylist file is git-crypt encrypted BY DESIGN — never inline its
  contents into anything that leaves the machine.
- `public` profile output is deterministic; do NOT wire L2 rewrites into a
  publish path (findings → denylist → deterministic re-render instead).
- Owner identity (V>>'s own public name/brand) is allowlisted policy, not a
  shield gap — see rules/no-sensitive-info-in-grip.md.

## Falsification

Wrong if it redacts allowlisted public brands (fix the allowlist), if a
denylisted identity reaches a governed surface (widen the matcher — never
abandon), or if public-profile output differs across runs on identical
input (determinism bug). Anchors: `tests/test_redaction_shield.py` (23
tests incl. Goodhart mutation anchors + fail-closed publish gate).
