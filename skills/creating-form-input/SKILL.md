---
name: creating-form-input
description: Documents Claude Code's form-input schema for AskUserQuestion — the typed question kinds choice, text and number — and the house contract that keeps every option explained after the schema stops requiring it. Use when a task needs typed or multi-field operator input, when an option list needs descriptions the newer schema marks optional, or when checking which of the two AskUserQuestion schemas a session was handed.
status: Active
effort: medium
version: 1.0.0
triggers:
  - typed or multi-field operator input
  - open-ended text question with no options
  - numeric quantity question with a range
  - "an option list rendered without explanations"
  - "which AskUserQuestion schema am I on"
integrates:
  - hitl
  - asking-users
  - creating-wizards
see_also:
  - hitl
  - asking-users
  - creating-wizards
  - hitl-decision-matrix
  - mcp-elicitation
---

# Creating form input

Claude Code ships **two** different schemas for the same `AskUserQuestion` tool, and
picks between them at runtime. A model is handed exactly one of them and has no view
of the other. This skill documents the newer one — the **form** — because GRIP's
existing human-in-the-loop skills all predate it and describe only the older
**dialog**.

The practical consequence is narrow and expensive. The form makes an option's
`description` optional and steers against writing one. Follow that steer and the
operator gets a column of bare labels with nothing explaining what any of them
would do.

## Two axes, not one list

GRIP's `/hitl` dispatch used to read as a flat list of four routes. It is really two
independent choices, and separating them removes a persistent confusion about
whether a wizard is "a different tool".

**Axis 1 — which schema the harness handed you.** There are exactly two, selected by
a runtime gate. You do not choose this; you detect it.

| | Dialog | Form |
|---|---|---|
| Interaction | Answering resolves it | Fill in, then Submit |
| Question kinds | choice only | `choice`, `text`, `number` |
| Option `description` | Required | Optional, discouraged |
| Skipping a question | Not offered | Leave any question unanswered |
| Free text | Automatic "Other" option | Type into any question |
| Heading and helper line | No | Yes, both optional |
| Asking for more questions | No | First-class field in the result |

**Axis 2 — the shape of the interaction you are building.** This is your choice, and
it is orthogonal to axis 1. One question; a sequence of related questions; a set of
pre-computed decisions to confirm; or a typed multi-field intake.

A wizard is **not a separate tool**. It is several `AskUserQuestion` calls batched
into one, on whichever schema you were handed. The genuinely distinct primitive is
MCP elicitation, which blocks a single tool call rather than the whole turn.

## The three question kinds

These are form-schema only. On the dialog schema, every question is a choice
question and the `kind` field does not exist.

**`choice` — omit `kind` entirely.** The default. Two to four options, each with a
`label`. The binary's own guidance: *"Prefer choices whenever the likely answers can
be listed."* Take that seriously — a text box is a worse experience than a good
option list almost every time, because it makes the operator compose an answer
instead of recognising one.

**`kind: "text"` — an open-ended question.** Renders a text box. It has **no options
at all**, which is why it is structurally unrepresentable in GRIP's older skills:
every one of them asserts options are mandatory. Takes a `placeholder`.

**`kind: "number"` — a quantity.** Renders a real numeric control. Requires `min`
and `max`. Optionally takes `step`, a `defaultValue` that must fall inside the
range, and a `unit` shown beside the value — the binary gives `"px"`, `"slides"` and
`"%"` as examples.

The form also gains one optional single-line heading above the whole set — the
binary's example is *"Before I build your deck"* — and an optional helper line under
each individual question.

## The house form contract

This is GRIP's rule, and it deliberately contradicts the schema's own guidance.

> **Every option carries a description, whatever the schema permits.**

The form schema documents `description` as:

> *"Optional: add only when the label alone would be ambiguous. One short line on
> what choosing it leads to."*

The failure is in who judges ambiguity. The model writing the option knows what it
meant, so a label reads as self-evident to the only party the guidance consults.
The operator reading it does not have that context. "Optional when unambiguous"
therefore resolves, in practice, to "usually omitted" — and the result is the bare
label column that started this whole investigation.

Three further house rules for forms:

**Never write "Other" or "Skip" as an option.** On the dialog the UI appends an
Other choice itself; on the form the operator can type into any question and leave
any question blank. Writing either one is redundant on both schemas.

**Previews are all-or-nothing.** When any option carries preview content the layout
switches to a side-by-side view — a vertical option list on the left, the focused
option's preview on the right — and the rows that would have carried descriptions
are gone. So either give every option a preview, or give none. The mixed state,
preview on one option only, is the worst of the three, and it is exactly the state
the schema's guidance nudges you toward.

**Act on a request for more questions.** The form's result can say the operator
asked for another round instead of answering. That is a capability, not an error
state, and the tool's own guidance instructs the model to call again with follow-up
questions before doing the task.

## Choosing a form or a dialog

You do not choose the schema — but you do choose the shape, and some shapes only
work on one of them.

Reach for typed input when the answer is genuinely a value rather than a selection:
a filename, a threshold, a count, a free-text label. Reach for choices whenever the
plausible answers can be listed, which is more often than it feels.

A form suits a several-field intake the operator can fill in any order and submit
once. A dialog suits a decision that should block until answered. If you find
yourself wanting a text box because listing the options is hard work, that is a
signal the question is under-scoped, not a signal to use `kind: "text"`.

## Detecting which schema you were handed

You cannot read the gate from inside a session. What you can do is check whether the
tool you hold accepts the form's fields — a `kind` on a question, a `placeholder`, a
`min` and `max` pair. If a question with `kind: "text"` and no options is accepted,
you are on the form. If option `description` is rejected as missing, you are on the
dialog.

Do not assert which schema is live from memory or from the schema text you were
given. That text describes the copy handed to you, and it can be flatly wrong about
what is on the operator's screen in a way neither party can detect from inside the
conversation.

## Worked example

A three-question intake mixing all three kinds. Note that every option carries a
description even though the form schema does not require it, and that the `text`
question has no `options` key at all.

```json
{
  "questions": [
    {
      "header": "Cache layer",
      "question": "Which caching layer goes in front of the read path?",
      "kind": "choice",
      "options": [
        {"label": "In-process LRU",
         "description": "Fastest, but each instance caches separately."},
        {"label": "Redis, shared",
         "description": "One shared cache, one more service to run."},
        {"label": "No cache for now",
         "description": "Ship without it and measure the real read load first."}
      ]
    },
    {
      "header": "Budget",
      "question": "What cache size ceiling should the LRU hold to?",
      "kind": "number",
      "min": 8,
      "max": 512,
      "step": 8,
      "defaultValue": 64,
      "unit": "MB"
    },
    {
      "header": "Eviction",
      "question": "What eviction behaviour do you want when the cache is full?",
      "kind": "text",
      "placeholder": "e.g. drop the oldest, or refuse new writes"
    }
  ]
}
```

Validate it before sending:

```python
from lib.hitl_quality_gates import validate_questions, QualityGateViolation

try:
    validate_questions(questions)
except QualityGateViolation as e:
    print(f"Gate violation: {e}")
```

`lib/hitl_quality_gates.py` is kind-aware. A `choice` question still requires two to
four options; a `text` or `number` question requires that `options` be **absent**,
not merely empty; and a `number` question requires both `min` and `max`. A question
with no `kind` is validated as a choice question, so every caller written against
the dialog schema behaves exactly as it did before.

## Falsification of this skill

This skill is wrong if any of the following holds.

**The house contract costs more than it saves.** If, over roughly twenty form-shaped
asks, writing a description for every option never once changes what the operator
picks — and the extra text measurably slows the exchange — then the schema's own
guidance was right and this contract should narrow to "describe any option whose
label names a trade-off".

**The two-axis model does not reduce mis-routing.** If sessions keep reaching for
the wrong primitive at the same rate as under the flat four-route list, the axes are
a distinction without a difference and `/hitl` should revert.

**The kinds stop being three.** The schema is a moving target — it was itself a
change to a tool that had one shape. A fourth kind, or the removal of `number`,
falsifies the enumeration here. Re-read the binary rather than patching this file
from a session's recollection of it.

**The detection advice proves unusable.** If probing the tool's accepted fields turns
out not to distinguish the schemas in practice, that section is wrong and should be
replaced by an honest "you cannot tell from inside".

Track: form-shaped asks that ship bare labels (target 0), and operator complaints
about unexplained options (target 0; the observed count that motivated this skill
was 1).
