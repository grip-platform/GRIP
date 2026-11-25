---
name: legal-ops
description: GRASP-native legal operations catalogue — playbook-aware contract review, privacy triage, claims substantiation, diligence, IP first-pass, litigation intake, regulatory watch, and AI-governance triage, each closed by a signed legal receipt (deterministic citation floor + IDR decision record + memory-chain belief record). Absorbed from anthropics/claude-for-legal (Apache-2.0) with every honour-system control replaced by a deterministic gate or a signed record. Use for any contract, compliance, risk, or legal-document work.
status: Active
effort: medium
version: 2.1.0
integrates:
  - legal-mode
  - legal-provenance
  - navigating-sa-divorce-procedure
  - prove-it
  - professional-pdf
  - sanitizing-public-releases
---

# Legal Operations — GRASP-native

> **Thesis: their playbook, GRIP's proof.** The workflow shapes and legal-domain
> content below are absorbed from `anthropics/claude-for-legal` (Apache-2.0,
> 12 practice verticals, cloned 2026-07-02). That suite's honesty mechanisms —
> source tags, spot-checks, verification logs, conflicts overrides — are prose
> discipline an eager model can silently violate. GRIP replaces each with a
> deterministic gate or a signed record: quotes prove via `lib/prove_it.py`,
> filings gate via `lib/legal_receipt.py`, decisions sign into the IDR forest,
> beliefs chain into the memory-chain. GRASP = Governed Reasoning And Signable
> Provenance — checkable by a skeptic without trusting us.

## The GRASP stack for legal work (SHIPPED)

| Layer | Mechanism | Where |
|-------|-----------|-------|
| Citation floor | Verbatim quote verification: verified / fuzzy / not_found — arithmetic, un-fakeable | `lib/prove_it.py` |
| Filing gate | Signed legal receipt; mechanical never-file-a-red (exit 1 on any NOT-FOUND or zero citations) | `lib/legal_receipt.py` |
| Decision record | HMAC-signed IDR leaf per checked deliverable (what was decided) | `lib/prove_it_provenance.py` → `lib/precog/idr.py` |
| Belief record | Signed memory-chain node cross-referencing the IDR (what was believed) | `lib/context_chain.py` |
| Model-agnostic drafting | HAL syscall with provider failover — any model, same floor | `lib/hal_llm_adapter.py` |
| Open protocol | HAPPI/1.3 `cite.verify` — the same deterministic check on any AI, any harness | `~/.hal/happi.md` |

The receipt is self-contained: a skeptic re-hashes sources (SHA-256), re-reads
each quote at its recorded offsets, and re-verifies the signed chains.

## Operations catalogue

Analysis frameworks run in-session under legal-mode's gates (jurisdiction →
track → provenance). Every operation that cites authority closes with a receipt.

| Operation | Shape (absorbed) | GRIP proof layer |
|-----------|------------------|------------------|
| Contract / vendor review | Playbook-aware review of NDAs, SaaS, vendor agreements — sales-side or purchasing-side; deviations from house positions flagged | Cited clauses → receipt; playbook deviations logged |
| Privacy triage | PIA vs mandatory GDPR DPIA vs proceed; DPA review | Regulation quotes → receipt |
| Launch / marketing claims | Substantiation check — every outward claim needs support | Claims → prove-it citations; unproven claims do not ship (`prove-it-claims-canonical`) |
| Diligence review | Issue extraction against house materiality; quote-per-finding | Every finding cites its clause verbatim → receipt |
| Employment review | Hire / termination / classification against the controlling standard, jurisdiction-specific | Standard quotes → receipt |
| Trademark / FTO first-pass | Knockout + confusion heuristics — explicitly NOT an opinion | Findings tagged `[review]` for counsel |
| Litigation intake / portfolio | Matter intake, status, holds, demand response | Intake decisions → IDR record |
| Regulatory watch | Feed diff against the policy library; open-gap listing | Diffs quote both texts → receipt |
| AI-governance triage | Use-case triage against a registry; EU AI Act per-system inventory | GRASP records ARE the Art. 12 audit trail — the moat is the compliance artefact |
| Redaction / PII shield | Deterministic redaction of client names, persons, emails, phones, IPs, hosts and matter refs from ANY outbound text — pinned pseudonyms keep the document coherent; `llm_egress` profile shields prompts BEFORE they reach a cloud provider and `deanonymize()` restores originals locally | `/redact` → `lib/redaction_shield.py` (L1 deterministic + optional L2 recall); span manifest is the audit record; publish gates fail CLOSED on an unreadable denylist |
| Family law (South African divorce) | Fact-free procedure playbook, run when the matter frame's topic is `divorce`: jurisdiction guard, Rules 41A and 43, the Divorce, Children's, Matrimonial Property and Maintenance Acts, evidence disciplines | `navigating-sa-divorce-procedure`: statutes fetched into the matter by `lib/legal_sources.py`, every quote proved by a receipt on the matter's private chain |

## Source-attribution tags (output contract)

Absorbed verbatim, then mechanised. **Provenance describes what happened, never
confidence** — "if you didn't retrieve it, it's model knowledge, no matter how
confident you are."

| Tag | Meaning |
|-----|---------|
| `[connector: NAME]` | The cite literally appeared in that connector's result this session |
| `[statute / regulator site]` | Retrieved from the official source |
| `[user provided]` | Supplied by the operator/client |
| `[web search — verify]` | Found via search; not yet authenticated |
| `[model knowledge — verify]` | The DEFAULT for anything not retrieved |
| `[settled — last confirmed YYYY-MM-DD]` | Carries the date the confidence was earned |
| `[review]` | Judgement call for the attorney |
| `[verify]` | Factual claim to confirm |

Tags are never stripped, collapsed, or promoted because a cite "seems right".

**Mechanisation (the GRIP upgrade):** every retrieved-source tag becomes a
prove-it citation (claim → exact quote → source) in the receipt spec.
`[model knowledge — verify]` NEVER enters a filed deliverable — retrieve the
source and prove the quote, or pull the claim.

**No-silent-supplement (three values, not two):** (1) supplement with a flag,
(2) stop and ask for the source, (3) flag-but-don't-use — surface known doubt
without letting it change the analysis. Silence about known doubt is as
misleading as confident assertion. **Currency trigger:** web search is REQUIRED
(not merely permitted) whenever the question would carry a "recent developments"
section in a firm alert.

## Matter workflow

1. **Classify** — contract review / compliance / dispute / IP (urgency + expertise per the table in legal-mode)
2. **Jurisdiction gate** — explicit, never defaulted (legal-mode Protocol 1)
3. **Conflicts check** — run before substantive work; a documented override is
   recorded as a signed IDR (non-repudiable, not YAML good-behaviour)
4. **Analysis** — with source-attribution tags on every authority
5. **Dual severity** — every finding scores legal risk × business friction;
   ambiguous mappings round UP; downstream skills carry upstream severity as a
   FLOOR (silent demotion is a contradiction a reviewing lawyer cannot see)
6. **Reviewer note** — single consolidated block (below)
7. **Receipt** — `"$G/bin/grip-py" "$G/lib/legal_receipt.py" spec.json --deliverable … --matter … --jurisdiction …` with `G="${CLAUDE_DIR:-$HOME/.claude}"`, so it runs from any folder; exit 0 required to file
8. **Close with options, not the decision** — a decision tree (draft-the-X /
   escalate / get-more-facts / watch-and-wait), plus "one question I'd ask that
   isn't in my checklist" (omit rather than manufacture)

**Matter spine.** The matter workspace this file once listed as roadmap is
realised by the private-repository pattern. A matter lives in a private,
git-crypt-encrypted repository: `matters/<code>.json` holds the frame (saved
there by `lib/legal_frame.py` when `GRIP_LEGAL_MATTER_STORE` names that
repository), beside the matter folder (`matters/<code>-<slug>/`, or the bare
`matters/<code>/` for a sensitive subject) with the index, evidence, notes, drafts,
fetched sources and the matter's own signed receipt chain (`--chain-dir`). Its
history is the repository's commits plus that chain.
`navigating-sa-divorce-procedure` section 3 shows the full layout.

## Reviewer-note contract

Exactly ONE consolidated block above every deliverable — collapsing to one line
when clean:

```
Sources: 2 connector-verified · 1 user-provided | Read: pages 1–50 of 200 |
Flagged: 3 [verify] · 1 [review] | Currency: checked 2026-07-02 |
Before relying: resolve the 3 [verify] items · receipt exit 0
```

The deliverable below the note is CLEAN — no banners, no meta-narration; it
reads like a partner wrote it. Quiet mode strips skill narration from
client/board-facing outputs while KEEPING the note and the tags. Never pretend
you read everything: a confident conclusion from a partial read is worse than a
sample honestly labelled.

## Consequential acts (human-gated)

Named acts require an explicit yes from the operator: sending redlines,
generating signature envelopes, serving a privilege log, filing, designating
documents. First-pass analysis never needs the gate; the consequential act does.
When the configured role is a non-lawyer, the gate produces a one-page attorney
brief (what was found, what to ask) instead of an install-anyway button.

## Privilege discipline

- **Jurisdiction-honest marking**: "attorney work product" is a US doctrine —
  asserting it on an EU document is a false assurance worse than no marking;
  in-house communications are compellable in EU competition matters (*Akzo
  Nobel*), so the no-flag tier is disabled there.
- **A header is a label, not a control**: before sending anywhere, classify the
  destination — privileged work product is never relayed to a chat channel
  without an explicit classification decision (extends the channel-scope gate).
- **One-way door**: under-marking waives (unrecoverable); over-marking is
  corrected in review — always prefer the recoverable error.
- Outputs INHERIT the privilege status of their sources.

## Disclaimer

Structured analysis frameworks with deterministic provenance — not legal
advice. The floor proves quotes are verbatim in their sources; it does not
prove a source is real, current, or good law, nor that it supports the
proposition. Always consult qualified counsel for binding decisions.

## Roadmap (DIRECTION — not shipped; per `rules/no-stubs.md` nothing here claims to exist)

- Prove-it-native tabular review engine (typed schema, quote-per-cell, 100% deterministic resolution — replaces sampled spot-checks)
- Conflicts + cross-matter-isolation PreToolUse gates (mechanical, not prompt discipline)
- Playbook-monitor: deviation-triggered, human-diff-gated playbook revision loop with IDR-signed proposals
- Legal skill-supply-chain trust layer (injection scan, update-time re-scan, SHA-pinned replacement)
- CourtListener research connector (provenance-bearing results prove-it can resolve)

## Attribution

Workflow shapes and legal-domain content absorbed from
`anthropics/claude-for-legal` (Apache License 2.0, © Anthropic PBC; cloned
2026-07-02). This file marks the modifications: provenance mechanisation —
GRASP receipts, IDR-signed decisions and overrides, deterministic citation
floor, HAL model-agnostic routing, HAPPI/1.3 protocol surface.

## Integration

Orchestrated by `legal-mode` (jurisdiction/track/provenance gates). Citation
engine: `legal-provenance` over `/prove-it`. Filing INFRA: `lib/legal_receipt.py`.
Used by business and legal modes.

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 2.1.0 | 2026-10-26 | Family-law row pointing at the navigating-sa-divorce-procedure playbook. The matter spine moved from the roadmap into the matter workflow, realised by the private-repository pattern. The receipt example runs through bin/grip-py |
| 2.0.0 | 2026-07-02 | GRASP-native rewrite: absorbed claude-for-legal operations catalogue + tag vocabulary + reviewer-note + privilege + consequential-act gates; receipt-closed workflow; honest roadmap |
| 1.0.0 | — | Initial thin framework (classify / jurisdiction / review / risk / output) |
