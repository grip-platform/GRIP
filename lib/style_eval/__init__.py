"""The paired, blind, two-judge evaluation that gates a change of GRIP's voice.

Three conditions answer the same held-out cases under the same isolated runner:
``baseline`` (the current output-styles/grip-canonical.md body), ``candidate`` (an
amended body) and ``comparator`` (the body of ayghri/i-have-adhd v0.4.1's SKILL.md).
Two non-Anthropic judges grade every (case, trial) group blind, under labels
permuted by a digest, against the rubric slice between the judge markers of
rubric.md. gate.py applies the pre-registered release gate
(docs/measurements/2026-10-18-adhd-style-eval-prereg.json) to each judge's rows and
combines the two verdicts. Every record is sealed with the sha256 of its canonical
JSON, as scripts/ste-readability-trial.py seals.

The design composes the method rulings of the falsified H778 trial
(docs/measurements/2026-10-05-ste-readability-trial-method-rulings.md, GRIP#7509):
held-out cases written before any run, a frozen rubric, judges that are not the
author's family, equal repeats per condition, a deleted-fact control
(lib/ste_lossless.planted_drops), a pre-registered non-inferiority margin, more than
one judge, and a stated aggregation rule. The source's harness
(ayghri/i-have-adhd, evals/) supplied the blinding, the isolation flags, the budget
metering and the resumability; GRIP took the influence and wrote its own.

Modules: generate (the runner), judge (the blind grader), gate (the verdict).
"""

STYLE_EVAL_VERSION = "1"
CONDITIONS = ("baseline", "candidate", "comparator")
DIMENSIONS = ("correctness", "autonomy", "actionability", "safety", "concision")
WEIGHTS = {"correctness": 0.35, "autonomy": 0.25, "actionability": 0.20, "safety": 0.10, "concision": 0.10}
#: The two exogenous judges, by their lib/sovereign_council.REGISTRY names.
JUDGES = ("deepseek", "gemini-pro")
#: The reader facts of the source (F1 to F5); every case names the ones it probes.
READER_FACTS = {
    "F1": "working memory is small",
    "F2": "knowing the answer is not doing it",
    "F3": "starting is the hardest step",
    "F4": "vague estimates all feel the same",
    "F5": "visible progress matters",
}
