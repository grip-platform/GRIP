---
name: idea
description: "Takes one idea from the operator and makes it the first record of a signed, replayable causal chain. It settles what the idea means first, and halts to ask the operator through /hitl when two readings are plausible. Then it records each consequence as a claim with a falsifier before the test, one verdict per claim, and what came of the idea. Each step is a signed IDR record in its own ledger, checked by the same forest verifier as the main decision chain. Use when the operator invokes /idea, or gives one idea to think through, test and act on."
user_invocable: true
command: idea
status: Active
version: 1.0.0
created: 2026-11-26
triggers:
  - /idea
  - I have an idea
  - test this idea
integrates:
  - hitl
  - asking-users
  - creating-wizards
  - precog-idr
  - prove-it
  - simple
  - hypothesis-driven-development
---

# /idea: one idea, a settled meaning, a signed causal chain

`/idea` takes one idea from the operator and records it as the root of a chain of signed records.
Before any other work, it settles what the idea means.
Each consequence becomes a claim with a falsifier, and each claim gets one verdict with its evidence.
The chain ends in an outcome: a merged change, an issue, a hypothesis, or nothing.

The tool is `lib/idea_chain.py`.
Run every command from the GRIP folder under `bin/grip-py`, never under a bare `python3` (`rules/never-system-python-for-grip.md`).
The records go to `state/precog/ideas.jsonl`, and git ignores that folder.

## The four rules, and what enforces each

| Rule | What the operator asked | What enforces it |
|---|---|---|
| A | One idea per prompt | `open` takes one text, and refuses a text with two or more `IDEA` headings |
| B | Ambiguity halts and asks through `/hitl` | `claim` and `outcome` are refused until a meaning is recorded, and a meaning with two or more readings is refused without the question and the verbatim answer |
| C | KISS | One module, one skill and one test file, with no new cryptography |
| D | The idea is the origin of a chain of idempotent, deterministic, transparent, tamper-evident, replayable records | Every node is a signed IDR record, a repeated step writes nothing, and `verify` checks the signatures and every rule |

Rule B uses the confidence bar of 99.9999999%, which is the operator's policy (CLAUDE.md rule 3), not a derived number.
Two plausible readings mean that confidence is below that bar.
So the gate counts readings: one reading settles the meaning, and two or more readings halt and ask.

## 1. OPEN: record the idea, verbatim

Write the operator's words to a file without change.
Then open the file.

```bash
bin/grip-py -m lib.idea_chain open --file "$SCRATCH/idea.txt"
```

The output gives the idea id, which has 16 hex characters.
A second `open` of the same words, with any change of spacing, writes nothing and gives the same id.
If the text holds two ideas, the tool refuses it.
Then ask the operator which idea comes first.

## 2. READINGS: list every plausible meaning

Write down every reading of the idea that a careful colleague could hold.
Keep a reading that is inconvenient.
Mark the reading you recommend, and give the reason in one sentence.

## 3. SETTLE: one reading settles, two or more halt and ask

With one reading, record it:

```bash
bin/grip-py -m lib.idea_chain meaning --idea <id> --reading "<the reading>"
```

With two or more readings, stop.
Ask the operator one question with `AskUserQuestion`, through route A of `skills/hitl/SKILL.md`.
Put each reading in an option, with the recommended reading first.
One question takes two to four options.
With more than four readings, ask in steps through `/creating-wizards`, or merge readings that lead to the same claims.
Then record the question and the operator's verbatim answer:

```bash
bin/grip-py -m lib.idea_chain meaning --idea <id> \
  --reading "<reading 1>" --reading "<reading 2>" --recommended 1 \
  --chosen 2 --question "<the question as asked>" --answer "<the answer, verbatim>"
```

If the operator answers in their own words, record those words with `--chosen-text` in place of `--chosen`.
The tool records one meaning for each idea.
A different meaning is a different idea, so open it in words that carry that meaning.

## 4. CLAIM: write each consequence and its falsifier before the test

A claim says what follows if the idea holds.
Its falsifier names the observed result that would show the claim false.
Record the claim before you run the test, so the record shows that the prediction came first.

```bash
bin/grip-py -m lib.idea_chain claim --idea <id> --claim "<what follows>" --falsifier "<the result that shows it false>"
```

To branch from an earlier claim, add `--parent <claim fingerprint prefix>`.
Keep to three to five claims, and choose claims that a real measurement can settle.

## 5. TEST: run the cheapest real measurement, then record the verdict

Run the measurement.
Keep the command and its output as the evidence.

```bash
bin/grip-py -m lib.idea_chain verdict --idea <id> --claim <claim fingerprint prefix> \
  --verdict CONGRUENT --evidence-file "$SCRATCH/evidence.txt" --verifier "<the command or the seal>"
```

The verdict is CONGRUENT when the claim held, and INCONGRUENT when the falsifier fired.
It is UNVERIFIABLE when no measurement could settle the claim.
A claim gets one verdict.
To test a claim again, write a new claim.

To quote a source, add `--quote "<exact words>" --source <file>`.
The tool stores a source inside the GRIP folder relative to that folder, so the verdict has the same fingerprint on every machine.
It stores a source outside the GRIP folder as given.
The tool refuses a quote that is not in its source, through `verify_quote` in `lib/prove_it.py`.

## 6. ACT: act on what survived

1. Open an issue or a small PR for a real defect, or for a change that GRIP needs.
2. Register a hypothesis with a numeric prediction for a claim that no measurement can settle yet (`skills/hypothesis-driven-development/SKILL.md`).
3. Register it in the live ledger only, never from a worktree.
4. Record each result as an outcome, with its references:

```bash
bin/grip-py -m lib.idea_chain outcome --idea <id> --outcome "<what came of it>" --status issue --ref GRIP#1234
```

The status is `merged`, `pr-open`, `issue`, `hypothesis` or `dropped`.
Use `merged` only after the merge is on `origin/main`.

## 7. VERIFY AND EXPLAIN

```bash
bin/grip-py -m lib.idea_chain verify --idea <id>
bin/grip-py -m lib.idea_chain show --idea <id>
bin/grip-py -m lib.idea_chain show --idea <id> --records
```

`verify` exits 0 only when every signature verifies and every rule holds.
It prints two numbers.
The idea digest is the same for any recording of the same steps.
The Merkle root commits to this one signed recording.
Then explain the chain to the operator through `/simple`: the idea, its meaning, what each test showed, and what came of it.

`bin/grip-py -m lib.idea_chain list` shows every opened idea and the counts that test this skill (see Falsification).

## What the records are

| Kind | What it holds | Its parent |
|---|---|---|
| `idea-root` | the operator's words, verbatim, and their sha256 | the anchor `human:idea-<id>` |
| `idea-meaning` | the readings, the recommended one, the chosen one, the question and the verbatim answer | the root |
| `idea-claim` | the claim and its falsifier | the meaning, or another claim |
| `idea-verdict` | the verdict, the evidence, the verifier and an optional checked quote | its claim |
| `idea-outcome` | what came of the idea, its status and its references | the meaning |

Each record is an ordinary signed IDR record from `lib/precog/idr.py`.
A record's fingerprint is the sha256 of the RFC 8785 form (`lib/jcs.py`) of its idea, kind, parent fingerprint and body.
No clock and no random value go into that fingerprint, so a step repeated with the same arguments finds it and writes nothing.
The ledger is a separate file, so the main decision chain's HEAD never moves and nothing reaches the public chain.

## How it follows HAPPI and GRASP

| Record | HAPPI | GRASP |
|---|---|---|
| `idea-root` and `idea-outcome` | 1.1 idr, a signed decision record | the decision leg |
| `idea-meaning` | 1.1 and 1.2 context: what was believed, and why | the belief leg |
| `idea-claim` | 1.4 continuity commitment, written before the action | a claim, registered first |
| `idea-verdict` | 1.4 verdict, and 1.3 cite.verify for a quote | the claim leg, proven |

The chain follows HAPPI 1.5 by what it leaves out.
It never records the agent's own reasoning as evidence, only claims and their evidence.
HAPPI 1.6 `decide` does not settle the meaning, because the operator settles it.

## Data limit

An idea in this ledger is internal or public text.
A personal matter goes to the guardian repository and never here (`rules/personal-matters-stay-private.md`).
A client's matter goes to that client's own private repository.

## Honest limits

- The chain is tamper-evident, not tamper-proof. A changed record fails its signature, but anyone who can write the file can delete a whole line. The signing key is on this machine.
- The tool cannot prove that an answer came from the operator. The session transcript holds that exchange.
- Rule B counts the readings you give it, so it is a speed bump, not a lock. If you list one reading, the gate passes, and `list` cannot see that.
- A verdict is only as good as the evidence recorded with it.
- `verify` reports a line it cannot read and a record moved to another idea. It cannot see a line that was cleanly removed.

## Falsification

This skill is wrong if, over its first 10 runs, the operator never picks a reading other than the recommended one.
Then the gate adds nothing, and it should become advice.
The skill is also wrong if no chain ever ends in a merged change.
`list` prints both counts: how often the gate fired with another reading chosen, and how many ideas ended in a merged change.
In `tests/test_idea_chain.py`, `test_two_readings_need_the_question_and_the_operators_answer` pins the gate: it turns red if anyone weakens the gate.
Hypothesis H1082 tracks the first count over the first 10 ideas.
