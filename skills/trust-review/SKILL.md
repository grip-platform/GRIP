---
name: trust-review
description: Run GRIP governance audit answering 6 accountability questions with evidence from the live installation. Use when demonstrating governance to prospects, auditing system health, or preparing for security reviews.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /trust-review
  - governance audit
  - trust review
  - accountability check
  - security governance
integrates:
  - trust_review.py
  - audit_chain.py
see_also:
  - security-mode
  - demo
  - sanitizing-public-releases
---

# Trust Review — Governance Audit Command

**Category**: Security / Governance / Demo
**Token Budget**: <3000
**Origin**: an article by a former client contact (Feb 2026) → commit `25fc3dd`

---

## Purpose

Runs 6 governance questions against the live GRIP installation and produces evidence-backed PASS/WARN/FAIL verdicts. Not self-reported values — real evidence from audit logs, gate configs, and hook files.

---

## Usage

```bash
/trust-review                   # Full governance report
/trust-review --question 4      # Single question drill-down
/trust-review --demo            # Annotated output for live demonstrations
/trust-review --legal           # Legal translation of each question
/trust-review --fix             # Recommendations for WARN/FAIL items
/trust-review --json            # Machine-readable output
```

---

## Protocol

### Step 1: Run the governance audit

```bash
python3 ~/.claude/lib/trust_review.py
```

Present the output in a formatted table:

| Q | Status | Question | Evidence |
|---|--------|----------|----------|
| 1 | [status] | Data flow | [summary] |
| 2 | [status] | Write access | [summary] |
| 3 | [status] | Defaults | [summary] |
| 4 | [status] | Enforcement | [summary] |
| 5 | [status] | Model constraints | [summary] |
| 6 | [status] | Accountability | [summary] |

### Step 2: Run audit chain verification

```bash
python3 ~/.claude/lib/audit_chain.py status
python3 ~/.claude/lib/audit_chain.py verify
```

Report chain integrity:
- Signing key: present/absent
- Last sequence number
- Chain: VERIFIED/BROKEN
- Signed vs unsigned entry count

### Step 3: Generate recommendations (always)

For each WARN or FAIL, output a concrete fix action:

| Status | Question | Fix |
|--------|----------|-----|
| Q2 WARN | Writes without provenance | Enable write provenance in `hooks/context-gate-hook.py` — add provenance hash to Write/Edit audit entries |
| Q5 WARN | CODE_WRITE_GATE inactive | Add `check_code_write_gate()` call to the PreToolUse hook chain |
| Q6 WARN | Identity not configured | Create `facts/identity.md` with operator name and signature |
| Q6 FAIL | Audit chain broken | Run `python3 lib/audit_chain.py verify -v` to locate break point, investigate tampering or log corruption |

### Step 4: Composite score

Calculate governance health as percentage:
- PASS = 1 point
- WARN = 0.5 points
- FAIL = 0 points
- Score = (total points / 6) * 100

Report as: `Governance Health: {score}% ({pass_count} PASS, {warn_count} WARN, {fail_count} FAIL)`

---

## Flags

### --demo

Annotate each output line for live demonstration. Add the "why it matters" context for each question. Highlight WARNs as honest governance (not bugs):

> "Notice the WARNs. A system that shows all green is governance theatre. These WARNs are the system telling the truth about its own gaps. That's the property you want."

### --legal

Append the legal translation table after the report:

| Trust Review Question | Legal Equivalent |
|---|---|
| Q1: Data flow | Did the AI read Client A's files while working on Client B's matter? |
| Q2: Write access | Can we prove no AI draft was altered after the partner approved it? |
| Q3: Defaults | What was the AI allowed to do by default without explicit instruction? |
| Q4: Policy not promises | Are these rules code-enforced or just guidelines the AI might ignore? |
| Q5: Model constraints | Can the AI draft and file something without a human in the loop? |
| Q6: Accountability | If a brief contains wrong information, can we trace which AI session produced it? |

### --fix

For each WARN/FAIL, run the fix interactively:
1. Show the fix action
2. AskUserQuestion: "Apply this fix? (yes/skip)"
3. If yes, execute the fix (edit config, create file, etc.)
4. Re-run the affected question to verify

### --question N

Run only question N (1-6). Useful for focused drill-down after a full report shows a specific issue.

### --json

Run `python3 ~/.claude/lib/trust_review.py --json` and output raw JSON. Useful for piping to case management systems or CI/CD pipelines.

---

## The 6 Questions — Technical Detail

### Q1: Data Flow
**Evidence source**: `logs/api-audit.jsonl` — counts Read tool entries from sensitive directories (`facts/`, `projects/`, `.env`)
**PASS**: Audit entries exist, sensitive reads tracked
**WARN**: No audit entries (logging may not be configured)

### Q2: Write Access
**Evidence source**: `logs/api-audit.jsonl` — counts Write/Edit entries with/without `provenance` field
**PASS**: Writes exist with provenance hashes (before/after SHA256)
**WARN**: Writes exist without provenance tracking

### Q3: Defaults
**Evidence source**: `config/default-gates.yaml` — counts gates enabled vs disabled
**PASS**: All gates enabled
**WARN**: Some gates disabled
**FAIL**: No gates configuration found

### Q4: Enforcement (Policy vs Promises)
**Evidence source**: `hooks/context-gate-hook.py` — counts `# GATE X:` patterns with deny actions
**PASS**: Mechanical gates found
**FAIL**: No mechanical enforcement gates

### Q5: Model Constraints
**Evidence source**: CLAUDE.md + settings.json — checks CODE_WRITE_GATE, RSI safety, de-anthropomorphism
**PASS**: All 3 constraints active
**WARN**: Some inactive
**FAIL**: None configured

### Q6: Accountability
**Evidence source**: `facts/identity.md` + audit chain (HMAC-SHA256 verification)
**PASS**: Identity configured AND audit chain cryptographically intact
**WARN**: One of the two is missing/broken
**FAIL**: Neither configured

---

## Audit Chain — How It Works

Each audit log entry gets three chain fields:

```
entry_hash   = SHA256(entry content, excluding chain fields)
chain_hash   = HMAC-SHA256(previous_chain_hash + entry_hash)
sequence     = monotonic counter
```

**Tamper detection**: Editing entry N changes its `entry_hash`, which breaks `chain_hash` at entry N+1, which cascades to every subsequent entry. The `verify` command walks all entries re-computing hashes.

**Key security**: HMAC signing key stored at `~/.claude/.audit-signing-key` (mode 0600, never committed). Without the key, forged entries cannot produce valid chain hashes.

**Concurrent safety**: `fcntl.flock` on `.audit-chain.lock` ensures two sessions cannot interleave entries out of sequence order.

### Known Discrepancy (live finding)

If `audit_chain.py status` shows a higher sequence than `verify` finds signed entries, the chain state file is ahead of the log. This happens when the audit log was rotated, truncated, or recreated after entries were signed. The trust review correctly reports this as a WARN on Q6 — the system is honest about the gap.

**Demo angle**: "Look — the trust review found that the chain state says 105 entries were signed, but the log only has unsigned entries. That's governance working — it caught the inconsistency."

---

## Demo Script (90 seconds)

```bash
# Step 1: Full governance report (30s)
python3 ~/.claude/lib/trust_review.py

# Step 2: Chain status (10s)
python3 ~/.claude/lib/audit_chain.py status

# Step 3: Full chain verification (20s)
python3 ~/.claude/lib/audit_chain.py verify

# Step 4: Single question drill-down (30s)
python3 ~/.claude/lib/trust_review.py --question 4

# Bonus: JSON for integration
python3 ~/.claude/lib/trust_review.py --json | jq '.[5]'
```

**Key demo line**: "Every legal AI should be able to answer: who wrote this, when, what did it read, and has anyone changed it?"
