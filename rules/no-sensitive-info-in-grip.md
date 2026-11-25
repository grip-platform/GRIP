# No Sensitive Info — Financial / Client-Commercial / PII — in the GRIP Repo (PARAMOUNT)

## The Rule

Sensitive **financial**, **client-commercial**, or **personal/PII** data
MUST NOT live in the GRIP repo (`~/.claude`, `grip-platform/GRIP`) — not
even git-crypt-encrypted. It goes in a **dedicated private repo** created
via `/sensitive-repo-bootstrap` with a **symmetric** git-crypt key (NOT
GPG-collaborator mode) and **no client-org remote**.

Covered data class (non-exhaustive):

- **Financial** — invoices, invoice history/sequence, the invoice
  generator, billing, rates / day-rates / retainers, bank/IBAN/SWIFT/
  payment details, VAT/tax numbers.
- **Client-commercial** — client commercial proposals, SOWs, quotes,
  pricing, scope documents; client meeting transcripts, named-individual
  deliverables, NDA-covered material, anything identifying a client's
  commercial position.
- **Personal / PII (third-party)** — real email addresses, phone numbers,
  physical/postal addresses, national ID numbers, passport numbers, dates
  of birth, tax/SSN numbers of any person who is **not** the repo owner or
  team. The owner's/team's own identity (`facts/team.md`, `facts/identity.md`,
  the operator's own `@codetonight.co.za` email) is the owner's own data and
  is allowed — it is not a third-party leak.

## Why git-crypt-in-GRIP is NOT sufficient (the load-bearing reason)

1. **No per-client key isolation.** GRIP's git-crypt is GPG-collaborator
   mode; the "dedicated `codetonight` key" wraps to the *same* GPG
   identity as the default key. A separate named key is cosmetic, not a
   second secret.
2. **The repo is mirrored to a client org** (a client remote). The
   key-wrap `.gpg` files AND every `prep/**` ciphertext blob are physically
   on the client's GitHub. Confidentiality = "no client GPG key enrolled"
   only — one `git-crypt add-gpg-user` away from full exposure.
3. **History is forever.** Once committed, ciphertext + filenames are in
   GRIP history on every mirror; a destructive rewrite to purge it is
   itself PARAMOUNT-forbidden blast radius
   (`feedback_private_repo_no_destructive_history_rewrite.md`).

A dedicated repo with a *symmetric off-repo key* and *no client remote*
removes all three: real key isolation, never on client infra, clean start.

## Mandate

- **New sensitive financial/commercial/PII artefact** → write it into the
  dedicated repo, never into `~/.claude`. Default target:
  `CodeTonight-SA/exampleorg-financials`
  (`~/CodeTonight/exampleorg-financials` on the second Mac, key
  `~/CodeTonight/.exampleorg-financials-git-crypt.key`; renamed from
  `exampleorg-financials` on 2026-11-24, and GitHub redirects the old
  name). New distinct
  client/scope → a fresh repo created via `/sensitive-repo-bootstrap` or
  `scripts/sensitive-repo-bootstrap.sh --dir <path> --repo <name>`.
- **Found such data already in GRIP** → migrate it out (copy to the
  dedicated repo, verify GITCRYPT on the remote), then `git rm` +
  `.gitignore` in GRIP go-forward. **Do NOT** rewrite GRIP history to
  purge historical blobs (low marginal risk vs PARAMOUNT blast radius —
  the council's surviving sound conclusion); accept historical ciphertext,
  stop the forward bleed.
- The invoice generator and its history file are co-located in the
  dedicated repo (`generate-invoice.py` uses `Path(__file__).parent /
  "invoice-history.json"`; output is the absolute `~/CodeTonight/
  PARAMOUNT_BACKUPS` — repo-location-independent, behaviour unchanged).

## Enforcement tier (knowledge-maturation-functor)

Rule-at-N=1 because severe (cross-client financial/PII exposure on a
client-mirrored repo). Review-and-habit enforced now. A mechanical
PreToolUse guard — `hooks/sensitive-info-guard.py` — is **NOW ACTIVE**,
wired into the `Edit|Write` PreToolUse block in `settings.json`. Severity
justifies early mechanical enforcement per the knowledge-maturation-functor
"rule now if severe" clause. Scope: content-scoped (financial **and PII**
markers under `~/.claude/prep/` or `~/.claude/exports/`) — NOT a blanket
directory block, to honour the over-block caution (prep/ also holds
non-financial sprint plans). The guard exempts obvious fakes (`example.com`,
`AKIA…EXAMPLE`) and the owner's own identity domains so demo docs and the
operator's own contact details are not blocked. First instance: this
migration (2026-05-19). Sibling:
`feedback_grip_hal_repo_protection_paramount.md` (access ≠ per-client
clearance), `rules/sync-mirror-safety.md` (allowlist-never-denylist for
what enters a shared repo).

## Falsification

Wrong if, over the next ≥3 sensitive-data events: the dedicated-repo
discipline provably prevents no exposure while measurably slowing
legitimate work (pure friction) → relax to advisory; OR GRIP stops being
mirrored to any client org AND git-crypt gains real per-client key
isolation (then in-GRIP git-crypt regains sufficiency — narrow scope, keep
the cross-ref); OR a dedicated repo is itself found mirrored to a client
(the mechanism, not the rule, is defective — fix the bootstrap); OR the
PII patterns false-positive on the owner's own identity more than they
catch real third-party leaks (then the owner-allowlist is mis-tuned —
widen it, don't drop the PII scope). Track: sensitive artefacts landing in
GRIP per month (target 0 after this rule); exposure incidents prevented vs
legitimate-work friction.

## Scope

Every session, every machine. The GRIP repo and any repo with a
client-org remote. Outside scope: non-sensitive operational metadata
(session context, telemetry, learning substrate) and the repo owner's/team's
own identity — those are explicitly de-crypted in `.gitattributes` for
non-interactive readability and are not third-party financial/commercial/PII.

Narrative (origin, incidents, measurements): `docs/doctrine/no-sensitive-info-in-grip.md`.
