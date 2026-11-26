---
name: mental-model
description: "Shows one up-to-date picture of GRIP in one command: sessions, memory, canonical git state, harnesses, decisions and spend, composed from the readers GRIP already has. Every source reports a status from a closed set, and every gap is named, never a silent zero. All text passes the redaction shield first. It promises a contract, not full coverage, so any report can stand in for another. Use when the operator invokes /mental-model or asks for the state of everything in GRIP at once."
user_invocable: true
command: mental-model
status: Active
version: 1.0.0
created: 2026-11-26
triggers:
  - /mental-model
  - what is the state of GRIP
  - give me the whole picture
integrates:
  - efficiency
  - holistic-memory
  - precog-idr
  - hypothesis-driven-development
  - idea
---

# /mental-model: one picture of GRIP, from the readers it has

`/mental-model` runs GRIP's existing readers together and prints one report.
It covers six axes: sessions, memory, canonical git state, harnesses, decisions and spend.
It writes nothing, makes no model call, and re-implements no reader.
The design is GRIP#9967, and the code is `lib/mental_model.py`.

## Run it

```bash
bin/grip-mental-model                 # the whole report
bin/grip-mental-model --axis memory   # one axis, which is still a whole report
bin/grip-mental-model --json          # the report and its manifest, as JSON
```

The report took 1.6 seconds when every source answered (measured 2026-11-26).
Each source has a time limit: 30 seconds, or 10 for HAL's gateway.
A source that does not answer in time becomes a `NOT_RUN` gap, so one slow source delays the report by at most its limit.
Every command a reader runs stops inside that limit, so no command keeps the process alive after the report prints.

## What it promises

Every report keeps these eight rules, whatever it covers.

1. Every source reports one status: OK, EMPTY, STALE, UNREADABLE, FORBIDDEN or NOT_RUN. Any status but OK prints a named line under GAPS, never a bare 0.
2. Every age carries its kind: heartbeat, mtime, ledger-lag or fetched-now.
3. Session ids are joined on their first eight hex characters. No source in this version joins them yet.
4. A list asks for 51 rows, and it says "at least 50" when the list was cut.
5. All text passes `lib/redaction_shield.py` with the public profile before the report is composed. If the shield cannot load, all text is withheld. No reader opens a transcript, the WhatsApp history or the body of a memory record.
6. Every git fact names its tree or its ref.
7. `--json` carries a manifest: the axes attempted, each source's status, and each source left out, with its reason.
8. It never runs `grip-anthropic-limits`, which spends a little of every account, or `grip-chain-anchor`, which is not read-only.

## Why a contract and not coverage

V>> decided this on 2026-11-26: "Contract -- as it has to fit in with LSP."
Under the Liskov rule, one report may stand in for another only if it keeps the same promises.
A report that promised full coverage would make every smaller report a broken substitute.
A report that promises the contract lets any report stand in for another, whatever it covers.
So each new version keeps every source of the version before it, and `tests/test_mental_model.py` pins that list.
The idea record behind this decision is `docs/measurements/2026-11-26-idea-lsp-dogfood.md`, merged in GRIP#9980.

## What this version covers

| Axis | Source | What it reads |
|---|---|---|
| sessions | fleet | `bin/grip-fleet-watch --json`, as counts only: transcripts, hot and stuck |
| memory | kernel | `bin/holistic verify`: does the kernel name every PARAMOUNT and feedback record |
| memory | index | the size of `MEMORY.md` against the length Claude Code reads |
| canonical | tree | `bin/grip-tree-check --json` on the tree it reads |
| canonical | origin-main | the remote's main against the local `origin/main` ref |
| canonical | open-prs | `gh pr list`, open pull requests on the tree's GitHub origin |
| harnesses | list | `bin/grip-harness list`, counted by tag |
| harnesses | hal-gateway | HAL's health check on port 4010 |
| decisions | chains | every ledger family under `state/precog/`, by file name only |
| decisions | hypotheses | the hypothesis ledger's totals |
| spend | weekly | the seven-day and five-hour use in HAL's cache-loss ledger |

The report names every other source the design lists under LEFT OUT, with its reason, and the two forbidden commands under NEVER RUN.

## Reading the report

- **OK** means the source answered.
- **EMPTY** means the source answered with nothing to report.
- **STALE** means the answer is real but old, or behind its canonical copy.
- **UNREADABLE** means the reader failed, and its error is the gap line.
- **NOT_RUN** means the reader did not answer within its time limit.
- **FORBIDDEN** is kept for a source the contract does not let the report read. This version reports none, and it lists its two forbidden commands under NEVER RUN.

## Honest limits

- The report says what the readers say, at each source's age. It does not check that a reader is right.
- The redaction shield removes only the shapes it knows: email addresses, phone numbers, message ids, keys and denylisted names. That is why no reader opens transcript or memory text in the first place.
- A reader written in pure Python has no time stop of its own. Every reader in this version reads only local files.
- Coverage is partial by design, and the report names every source it leaves out.

## Falsification

This skill is wrong if, over its first 10 uses, the report cannot answer more than half of the operator's questions about GRIP's state.
Then the coverage gap matters, and the next version must add the missing sources.
It is broken, not wrong, if a source with a status other than OK ever prints no gap line. `tests/test_mental_model.py` pins that rule.
