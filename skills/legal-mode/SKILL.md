---
name: legal-mode
description: Orchestrates legal-ops dual-track workflows with jurisdiction awareness, quality gates, and the GRASP provenance gate — every legal deliverable that cites authority ships with a signed, self-verifiable legal receipt (never file a red). Use when /mode legal is active and legal document work begins.
status: Active
effort: medium
version: 2.2.0
triggers:
  - /mode legal
  - legal mode active
integrates:
  - legal-ops
  - legal-provenance
  - navigating-sa-divorce-procedure
  - prove-it
  - sanitizing-public-releases
  - asking-users
  - creating-wizards
  - hitl
  - hitl-decision-matrix
---

# Legal Mode Orchestration

Routes legal work through dual-track generation with jurisdiction, quality, and
**GRASP provenance** gates. Orchestration layer only — see `legal-ops/SKILL.md`
for the operations catalogue and `legal-provenance/SKILL.md` for the citation
engine. GRASP = Governed Reasoning And Signable Provenance: what the AI decided,
what it believed, and proof of every claim — checkable by a skeptic without
trusting us.

## Protocols

### 0. Activation Onboarding (runs first, every activation)

Legal mode establishes a **matter frame** before any work. Getting the frame
wrong is the expensive error class: wrong jurisdiction means the wrong law
entirely, and wrong posture inverts every default in the document.

Deterministic backend: `lib/legal_frame.py` (branch rules, tiers, storage).
Never reimplement its logic in prose — call it.

**Step 1 — read the state.**

```bash
G="${CLAUDE_DIR:-$HOME/.claude}"
"$G/bin/grip-py" "$G/lib/legal_frame.py" status
```

Returns store availability, saved matter count, the most recent frame line, the
installed topic playbooks (`topic_playbooks`), and the operator's configured
jurisdiction shortcuts. The commands in this skill name the GRIP install through
`CLAUDE_DIR`, so they work from any folder, a private matter repository included.

**Step 2 — offer resume.** If a recent matter exists, show its frame line and
offer one-keystroke resume. Confirm resumes it; decline starts clean. The frame
line is shown so a stale frame is visible rather than silent.

**Step 3 — run the adaptive wizard.** Two questions always, more only when the
answers make them load-bearing. Ask via AskUserQuestion, one call, batched.

| Order | Question | Asked when |
|-------|----------|-----------|
| 1 | **Jurisdiction** — which law governs | Always |
| 2 | **Task** — Draft / Review / Explain / Research | Always |
| 3 | **Posture** — our side / their paper / neutral | Draft or Review |
| 4 | **Destination** — internal / counterparty / filed | Draft, Review, Research |
| 5 | **Sensitive data** — real names, amounts, parties | Destination is counterparty or filed |
| 6 | **Topic** -- divorce / contract / privacy / employment / other | Optional: offered only when `plan` reports a non-empty `topic_playbooks` |

Task meanings: **Draft** = new paper going out from you. **Review** = their
paper landed on you. **Explain** = interpret existing text, no document
produced. **Research** = find and cite authority.

So "what does this indemnity clause mean" stays at two questions, and a filed
draft with client detail deepens to five. Confirm the branch with
`"$G/bin/grip-py" "$G/lib/legal_frame.py" plan jurisdiction=... task=...` rather
than reasoning it out — it returns `required`, `optional`, `next`, `plan`,
`frame_line`, `playbook` and `topic_playbooks`.

**Topic is optional and never changes the frame.** It is not a required
question, it never changes the tier, and it is offered (after the required
questions) only when `plan` lists it under `optional`, which happens only when a
topic playbook is installed. Pass the answer as `topic=<word>`; any lowercase
word is valid. Its only effect is to select that topic's fact-free playbook
(Step 7).

**Jurisdiction is never pre-filled and never defaulted.** Offer the configured
shortcuts if any exist, otherwise ask as free text. Naming a likely
jurisdiction — even to confirm it — recreates a default by anchoring instead of
by configuration, which is exactly what was removed from
`modes/definitions/legal-mode.yaml`. Shortcuts live in
`config/legal-jurisdictions.local.json` (gitignored, never shipped); the
committed `.example.json` is deliberately empty.

**Questions 2 to 5 may be pre-filled** by `/ultrareason` reading the operator's
one-line description of the matter, on Pool-B so it costs nothing. Pre-filled
answers are presented for confirmation, never applied silently.

**Step 4 — announce the plan before spending it.** The frame determines the
workflow tier and Broly level (`workflow_tier()`), so state which will run
before running it.

**Step 5 — state the honesty boundary once**, at frame-set, in plain English:
the citation floor proves quoted words appear verbatim in the supplied source;
it does not prove the source is real, current, good law, or that it supports
the proposition. That judgement stays with the lawyer. Do not repeat this on
every deliverable.

**Step 6 — save the matter.** Frames persist by name in a dedicated private
repo outside the GRIP repo. If the store does not exist, `save()` refuses and
prints the exact `/sensitive-repo-bootstrap` command. It never falls back to a
writable location, because the only writable location nearby is the GRIP repo
itself, which is mirrored to a client org. It also refuses a store inside any
GRIP clone or worktree, and a frame that git would not route through git-crypt,
so a frame is never written as plaintext.

**Personal matters.** A personal matter's frame belongs in the operator's
private repository, beside the matter's own folder. Before Step 1, run
`export GRIP_LEGAL_MATTER_STORE=<private repository root>`; the root is the path
held under `guardian_repo` in the gitignored `config/guardian.local.json`. The
frame then saves as `matters/<code>.json` in that repository.

**Step 7 -- hand over to the topic playbook.** When `plan` reports a `playbook`,
read `skills/<playbook>/SKILL.md` and run its applicability check (its
jurisdiction guard) before any substantive work. If the guard says the playbook
does not apply, say so and continue without it. A topic with no installed
playbook changes nothing: the frame works exactly as before.

**Skipping.** `--quick` bypasses the onboarding for a throwaway question. The
frame then records `skipped: true`, so nothing downstream mistakes an absent
frame for a confirmed one. Never infer a frame that was skipped.

**Reframe.** Any answer can be changed mid-matter; re-run the affected branch
and re-save. The frame line always reflects current state.

### 1. Jurisdiction Gate

Satisfied by Protocol 0 when the onboarding has run. It remains the standalone
gate for work that arrives without a frame:

1. **HALT** — jurisdiction must be explicit
2. AskUserQuestion: "Which jurisdiction governs this document?"
3. Options: whatever `load_jurisdictions()` returns, plus free text. Never a
   hardcoded list — a shipped list of jurisdictions both anchors the answer and
   discloses where the operator does business.
4. Set governing law and court jurisdiction throughout

No default jurisdiction. Every engagement starts with explicit selection.

### 2. Dual-Track Routing

| Signal | Track | Output |
|--------|-------|--------|
| "formal contract", "court-ready" | Track 1: Formal | Senior attorney-level contract |
| "explain this", "simplified" | Track 2: Simplified | Plain language companion |
| "both versions", "full package" | Dual | Formal + Simplified pair |
| Ambiguous | HALT | AskUserQuestion for track selection |

### 3. GRASP Provenance Gate (PARAMOUNT)

Every deliverable that **cites authority** (statute, judgment, contract clause,
regulation) passes the deterministic citation floor before it leaves the session:

1. Build the citations spec (claim → exact quote → source) per
   `legal-provenance/SKILL.md`.
2. Issue the receipt:
   ```bash
   G="${CLAUDE_DIR:-$HOME/.claude}"
   "$G/bin/grip-py" "$G/lib/legal_receipt.py" spec.json \
     --deliverable memo.md --matter "$MATTER" --jurisdiction "$JURISDICTION"
   ```
3. **Exit 0 = SAFE TO FILE** (every quote verbatim-present in its cited source).
   **Exit 1 = DO NOT FILE** — a fabricated/embellished/misattributed quote
   renders NOT-FOUND and the gate is mechanical: never file a red.
4. The receipt JSON accompanies the deliverable. It is self-contained: a skeptic
   re-hashes the sources (SHA-256), re-reads each quote at its recorded offsets,
   and re-verifies the signed IDR + memory-chain records — without trusting GRIP.

Recording is mandatory, not optional (`rules/cryptographic-causation.md`): the
receipt writes a signed IDR decision leaf + memory-chain belief node. `--no-record`
exists for previews only — a production deliverable always records. A matter whose
record must stay out of GRIP's shared chain (a personal or a client matter) still
records, onto a chain of its own: `--chain-dir <folder>`. The receipt's `chain.ledger`
then reads `own`, which tells a verifier where to look.

**Honesty boundary (state it to the client):** the floor proves the quoted words
are verbatim in the supplied source. It does not prove the source is real,
current, good law, nor that it *supports* the proposition — that judgement
remains the lawyer's. The floor removes the indefensible error class
(fabricated text); the lawyer keeps the lawyering.

### 4. Quality Validation

Every generated document must pass:

| Check | Requirement |
|-------|-------------|
| Contra proferentem | Zero ambiguous terms — every clause has ONE interpretation |
| IP indemnification | UNCAPPED for IP infringement claims |
| 11 sections present | Title, Recitals, Definitions, Scope, Fees, IP, Confidentiality, Indemnification, Limitation, Term, General |
| **Citations proven** | **0 NOT-FOUND on the legal receipt (`lib/legal_receipt.py`) — never file a red** |
| Security scan | No real names, amounts, or account numbers in version-controlled output |
| Jurisdiction clause | Matches gate selection from Protocol 1 |

### 5. Security Gate

Before any file write or commit:

- Replace real values with placeholders — MECHANICALLY, via the canonical
  shield: `/redact` (`lib/redaction_shield.py`). Denylist entities become
  pinned pseudonyms (`[ORG-1]`, `[PERSON-1]`); emails/phones/IPs/JIDs hit
  the generic floor. Manual `[CLIENT_NAME]`-style substitution is the
  fallback only when the shield is unavailable.
- Client-context prompts leaving for a cloud LLM go through the
  `llm_egress` profile first — the model sees stable pseudonyms (context
  preserved), `deanonymize()` restores originals locally.
- Run `sanitizing-public-releases` if output destined for repository
- HALT if sensitive data detected
- Client matter data never enters the GRIP repo (`rules/no-sensitive-info-in-grip.md`)

## Model-Agnostic Drafting (HAL)

Drafting and analysis may route through any provider via the HAL substrate
(`lib/hal_llm_adapter.py` — the canonical AI syscall, automatic failover). The
provenance floor is deliberately **model-independent**: `cite.verify` is
arithmetic string-matching specified at protocol level (HAPPI/1.3), so the same
un-fakeable check holds whichever model drafted the words. Provability is a
property of the substrate, not of one model.

## Workflows

### Quick (NDA, single clause review)

1. Jurisdiction gate
2. Single-track generation (formal only)
3. Quality validation
4. GRASP receipt (if any authority cited) — exit 0 required
5. Output + receipt

**Broly level**: THREAD (de-escalated)

### Standard (service agreement, dual track)

1. Jurisdiction gate
2. AskUserQuestion: track selection
3. Generate selected track(s)
4. Quality validation per track
5. Cross-reference formal and simplified for consistency
6. GRASP receipt per track — exit 0 required
7. Output + receipts

**Broly level**: FORK (default)

### Deep (multi-party, cross-jurisdiction)

1. Jurisdiction gate per party
2. Conflict-of-law analysis
3. Parallel drafting via Broly CLUSTER
4. Cross-party consistency review
5. Quality validation per document
6. GRASP receipt per document — exit 0 required, one receipt per filing
7. Consolidated output package + receipt bundle

**Broly level**: CLUSTER (escalated)

## AskUserQuestion Triggers

Mandatory HALT and ask when:

- Jurisdiction not specified or ambiguous
- Liability cap amount not provided
- IP ownership split between parties unclear
- Track selection (formal vs simplified vs both) not stated
- Termination notice period not specified
- Multiple governing laws in play
- Confidentiality duration not specified
- A receipt shows NOT-FOUND citations and the operator asks to proceed anyway
  (the answer is no — fix the citation or pull it; the gate is mechanical)

## Output Style

- British English throughout
- Formal register for Track 1 (legal Latin where it adds precision)
- Plain language for Track 2 (short sentences, active voice, visual aids)
- No emoji in any legal output
- Section numbering: 1., 1.1, 1.1.1 hierarchy
- Receipts are shipped as `<deliverable>.receipt.json` beside the deliverable

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 2.2.0 | 2026-10-26 | Optional matter topic (Protocol 0, row 6): never required, never changes the tier, offered only when a topic playbook is installed. Step 7 hands over to that playbook, whose own jurisdiction guard runs first. `plan` also reports optional, playbook and topic_playbooks. GRIP_LEGAL_MATTER_STORE puts a personal matter's frame in its private repository. Examples run through bin/grip-py, never system python3 |
| 2.1.0 | 2026-07-25 | Activation onboarding (Protocol 0): adaptive matter frame via lib/legal_frame.py — 2 mandatory questions branching to 5, jurisdiction never pre-filled or defaulted, named matter frames stored outside the GRIP repo, tier announced before spend, honesty boundary stated once. Removed the hardcoded jurisdiction list from Protocol 1 (it both anchored the answer and disclosed working jurisdictions in a shipped file) |
| 2.0.0 | 2026-07-02 | GRASP provenance gate (Protocol 3): mandatory signed legal receipt via lib/legal_receipt.py, never-file-a-red mechanical, HAL model-agnostic drafting note, receipt steps in all three workflows |
| 1.0.0 | 2026-02-09 | Initial creation — orchestration layer for legal mode |
