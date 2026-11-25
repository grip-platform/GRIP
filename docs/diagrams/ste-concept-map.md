# ASD-STE100 in GRIP: a concept map

Simplified Technical English (ASD-STE100) is a writing standard for aircraft maintenance manuals. GRIP uses 80% of it as the measure of its own writing voice.

This page shows how the parts of the standard connect to the parts of GRIP. Read the graph from left to right. The standard is on the left. GRIP's tools are in the middle. The tests of the idea are on the right.

Two facts matter most. The first fact is that GRIP ships none of ASD's dictionary. The repository holds a builder, and each operator builds a local dictionary from their own copy of the standard. The second fact is that each claim has a test. Each hypothesis in the graph names the tool that measures it.

```mermaid
flowchart LR
  subgraph STD["ASD-STE100, Issue 9: the nine sections of rules"]
    direction TB
    R1["1 Words"]
    R2["2 Noun clusters"]
    R3["3 Verbs"]
    R4["4 Sentences"]
    R5["5 Procedures"]
    R6["6 Descriptive writing"]
    R7["7 Safety instructions"]
    R8["8 Punctuation and word counts"]
    R9["9 Writing practices"]
  end

  subgraph LIM["The five limits"]
    direction TB
    L1["An instruction: 20 words"]
    L2["A description: 25 words"]
    L3["A paragraph: 6 sentences"]
    L4["A noun cluster: 3 words"]
    L5["One instruction in a sentence"]
  end

  subgraph VERB["The verb forms"]
    direction TB
    V1["Approved: command, simple present, simple past, simple future, infinitive, past participle as adjective"]
    V2["Not approved: progressive, perfect, passive in a procedure"]
  end

  subgraph DICT["The dictionary: four kinds of word"]
    direction TB
    D1["Approved words"]
    D2["Unapproved words, each with its alternative"]
    D3["Technical names"]
    D4["Technical verbs"]
  end

  subgraph GRIP["GRIP's tools"]
    direction TB
    G1["lib/ste_lint.py: the linter and the score"]
    G2["lib/ste_lexicon.py: the seed and the local dictionary"]
    G3["/clarify, /plain-language and /plain: a rewrite on request"]
    G4["scripts/ste-baseline.py: the score of real turns"]
    G5["hooks/ste-lint-hook.py: one score line for each turn"]
    G6["The four voice channels: the rule, the preset, the session directive, CLAUDE.md"]
    G7["scripts/ste-readability-trial.py: the trial"]
  end

  subgraph HYP["The hypotheses"]
    direction TB
    H778["H778: readers prefer STE, and no fact is lost"]
    H779["H779: the pass rate reaches 0.95"]
    H780["H780: the hook sends nothing out"]
    H781["H781: the operator prefers the Task Card"]
  end

  subgraph LADDER["Karpathy's ladder"]
    direction TB
    K1["STE writing"] --> K2["A diagram"] --> K3["A web page"] --> K4["An explainer video"]
  end

  R5 --> L1
  R5 --> L5
  R6 --> L2
  R6 --> L3
  R2 --> L4
  R3 --> VERB
  R1 --> DICT
  R4 --> G1
  R7 --> G6
  R8 --> G1
  R9 --> G1
  LIM --> G1
  VERB --> G1
  DICT -- "ASD's list is never in the repository" --> G2
  G2 --> G1
  G1 --> G3
  G1 --> G4
  G1 --> G5
  G1 --> G6
  G7 --> H778
  G4 --> H779
  G5 --> H780
  G6 --> H781
  G3 --> K1
```

## How to read the graph

- **The nine sections** come from the overview sheet of the standard. The arrows show which section sets which limit.
- **The licence boundary** is the labelled arrow. ASD owns the dictionary. GRIP's repository holds a short seed that GRIP wrote and a builder. The local dictionary is built from the operator's own copy.
- **The linter** is the centre. Each other tool reads its score.
- **The ladder** is from Andrej Karpathy's note of 2 October 2026. Clear writing is its first step. A diagram, a web page and an explainer video are the next steps.

## Where each tool is

Each G box of the graph is one row of this table, under the same name.

| Tool | Where | What it does |
|---|---|---|
| The linter and the score | `lib/ste_lint.py` | It finds the faults in a text and gives the text a score. |
| The lexicon | `lib/ste_lexicon.py`, `config/ste/grip-seed.json` | It holds GRIP's seed and builds the local dictionary. |
| The rewrite on request | `/clarify`, `/plain-language`, `/plain` | It rewrites a text to the STE rules when you ask. |
| The baseline | `scripts/ste-baseline.py` | It scores the last 200 real turns and prints numbers only. |
| The hook | `hooks/ste-lint-hook.py` | It scores each turn and shows the operator one line. |
| The trial | `scripts/ste-readability-trial.py` | It asks model judges which version reads better and which keeps the facts. |
| The voice channels | `rules/canonical-output-style.md`, `output-styles/grip-canonical.md`, `hooks/caveman-session-default.py`, `CLAUDE.md` | They tell each session how to write. G6 in the graph names the same four files: the rule, the preset, the session directive, CLAUDE.md. |

## The two switches of the hook

The hook shows one line at the end of each turn. An example is `STE: 75% of sentences pass the sentence rules. 3 unapproved words, 1 sentence too long.`

- `GRIP_STE_VISIBLE=0` hides the line. The hook still writes its row of numbers to `logs/ste-lint-hook.jsonl`.
- `GRIP_STE_FEEDBACK=1` also gives the line to the model with the next prompt. This switch is off by default.

The second switch is off by default. A score that the model reads each turn changes how the model writes. The trial of hypothesis H778 did not confirm that change, so the operator decides.

## What the trial found

The trial ran on 2026-10-05, on 20 paragraphs from GRIP's own documents. Each paragraph had an STE rewrite.

Two model judges read each pair. DeepSeek v4 Pro preferred the STE version in 16 of 20 pairs. GPT-6 Astra preferred it in 19 of 20 pairs.

Each judge also answered three questions for each pair, from each version. GPT-6 Astra got 58 of 60 answers right from each version. DeepSeek v4 Pro got 55 right from the STE versions and 58 right from the originals.

The rule was registered before the trial: a loss of accuracy falsifies the hypothesis. So H778 is FALSIFIED by its rule. Two reviews of the method found that each lost fact is present in its rewrite. They also found that the rule is too strict for this test. A new trial needs a new hypothesis, and GRIP#7509 holds its design. The operator decides if the voice changes.

The records are in `docs/measurements/`. Their names start with `2026-10-05-ste-readability`.

GRIP is not certified or endorsed by ASD. ASD-STE100 is a trademark of ASD.
