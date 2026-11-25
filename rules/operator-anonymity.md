# Operator Anonymity — the doctrine

> **Anonymity is operational, not aspirational. Every piece of the operator's real-world identity that enters the repo — a JID, a browsed URL, a name, a number — is a leak waiting to happen. It is either removed, or it will surface.**

## What anonymity means here — concretely

The operator is identified today by a chain of signals that are strewn across the repo, memory, and runtime state. Each is a leak surface:

| Surface | What leaks | Fix |
|---|---|---|
| Runtime absorb logs (`data/absorbed-*.jsonl`) | Browsed URLs, channel JIDs, scrape text | Gitignored + untracked now; audit for the same class |
| WhatsApp session files (`.wa-session*`, `.wa-pending-sends.json`) | Session/encryption keys, JIDs | Local-only, never committed; confirm git-crypt covers them |
| `facts/identity.md`, `team.md`, ratifier configs | Full name, email, JID | Minimise to a pseudonym + rotate the JID as the identity moves |
| Git history | Everything ever committed, even if later deleted | Purge-class commits (see the absorb-log incident) must never exist; treat history as permanently public |

## The principle

1. **Runtime data is not source.** Anything a process writes while running (logs, absorbs, session state, caches) is runtime data and is gitignored or it is a leak. There is no third option.
2. **A JID is a name.** The operator's WhatsApp JID is not a handle — it is the operator's identity in every absorb, log and config it appears in. It is therefore not written in this repo: running code reads it from the untracked `config/wa-identities.local.json` through `lib/wa_identities.py`. As anonymity becomes real, the JID itself rotates.
3. **The repo is presumed public.** Even a private repo with "a few people" becomes public the moment it is forked, mirrored, or a collaborator leaks. Treat every commit as though it will be read by a stranger.
4. **History is permanent.** Deleting a file from HEAD removes it from the present, not from the past. The only fix for a committed secret is to never commit it — or to rewrite history (which this rule's incident avoided only because the leak stayed off `main`).

## Falsification

This rule is wrong if, over the next month: a runtime-data file containing the operator's JID or browsing history is committed again; OR an identity signal is added to the repo without first asking "what happens when this is public?". The test before every commit that touches personal data: *would I want this read back to me by a stranger?* If the answer is no, it does not get committed.

## Composition

- `rules/productise-the-proof.md` — the sibling doctrine; anonymity is the same honesty, applied to the operator instead of the product.
- The absorb-log incident (2026-09-17) — the worked example this rule is extracted from: tracked plaintext, two branches, zero on main.

Narrative (origin, incidents, measurements): `docs/doctrine/operator-anonymity.md`.
