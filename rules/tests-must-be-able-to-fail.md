---
paths:
  - "tests/**"
  - "**/tests/**"
  - "**/test/**"
  - "**/__tests__/**"
  - "**/conftest.py"
  - "**/test.*"
  - "**/test_*"
  - "**/test-*"
  - "**/*_test.*"
  - "**/*-test.*"
  - "**/*.test.*"
  - "**/*.spec.*"
  - "**/*_spec.*"
  - "**/*Test.*"
  - "**/*Tests.*"
---

# Tests Must Be Able To Fail — the green-test-holding-a-defect-in-place class

Origin: 2026-07-25 → 2026-08-02, five independent instances across GRIP, HAL and
hal-py, plus a sixth in GRIP on 2026-08-19. N=6, well past the N=3 threshold at which
`knowledge-maturation-functor.md` promotes an observation to canonical doctrine.

## The class

A test can be **green while the thing it guards is broken, missing, or actively
wrong** — and in the worst form, green *because* it is wrong. Four shapes, in
ascending severity:

1. **It cannot notice.** The test pins a spelling to a spelling and never asks
   whether the named thing still works.
2. **It tests the wrong artefact.** The assertions are correct, but they are
   applied to a file that is not the one under change.
3. **It cannot fail at all.** The assertion is tautological — true regardless
   of program state — so it is not a weak check but *no* check, wearing the
   syntax of one. A subtler form: the assertion reads a field out of a record
   the code under test just wrote, and that field is populated from a constant
   — so it cannot disagree with itself. This shape says what NOT to assert;
   what to assert instead is the artefact the operation acted on, per
   `verify-canonical-not-claimed.md` §"Self-Describing Artefacts Are Claims,
   Not Evidence".
4. **It requires the defect.** The assertion demands the broken behaviour, so a
   correct fix turns the suite red and looks like a regression.

Shape 4 is qualitatively worse than the rest. A test that merely fails to catch
a bug is a gap. A test that *specifies* the bug is an active obstacle: it
converts "fix the defect" into "break the build", and the natural response —
trusting the suite — preserves the defect.

Shape 3 is the one that hides in plain sight, because unlike 1 and 2 it needs no
drift and no environment quirk — it is wrong the moment it is typed, and it
survives review because reviewers read assertions for *intent* rather than
parsing them. Added 2026-08-19 after an instance in this very rule's own
enforcement (row 6 below).

## The instances

| # | Where | Shape | What it did |
|---|---|---|---|
| 1 | `test_agora_sovereign_roster.py` | 1 | Pinned `ids[:3] == ["deepseek-chat", ...]`. The model was retired; AGORA's reviewer returned HTTP 400 on every run and the test stayed green, because it never asked whether the model answered. |
| 2 | `test_agora_judge.py:295` | 4 | Required `--label pr-9999-followup`. Nothing creates per-PR labels and `gh issue create` aborts on an unknown one, so **every** DEFER follow-up had failed since the feature shipped. Proof it never once worked: an external search returned 0 such issues ever created. |
| 3 | HAL `test_happi_context_v11.py` | 2 **and** 4 | Asserted `["started", "context", "completed"]` — the exact ordering the spec's falsification clause names as proof the design is wrong — *and* resolved the runtime from `Path.home()`, so it graded the developer's home directory rather than the checkout. Reported **16/16 green while never executing the fixed code**. |
| 4 | GRIP `test_auto_converge_check.py` | 4 | Asserted a missing cache returns `0.0`, encoding the exact conflation that made the RSI convergence criterion unsatisfiable by construction. |
| 5 | HAL `test_happi_envelope_v11.py` | 1 | Asserts `happi/1.2` "MUST be rejected" — a v1.1-era closed set. The parser is current; the tests are stale. |
| 6 | GRIP `test_grip_tree_sync.py:131` | **3** | `assert not (git_dir / "MERGE_HEAD").exists() or True` — parsed as `assert ((not exists) or True)`, so the headline check of a test named `..._leaving_the_tree_clean` could never fail. Written *while enforcing this rule on other tests*, and caught not by its author but by an AGORA council on PR #4983. Confirmed dead: after the fix, disabling `merge --abort` in the shim turns it red; before, nothing could. |

Instance 3 is the sharpest lesson: **two shapes concealed each other** (2 and 4). The
wrong-artefact fault meant the requires-the-defect assertion was never
challenged, and the requires-the-defect assertion meant fixing the path alone
would have surfaced as a regression. Neither was visible while the other stood.

## Mandate

**1. Ask the disqualifying question of every test you write or trust:**

> *Could this pass while the feature is one hundred percent dead?*

If yes, it pins a spelling. Assert the **property** that made the thing worth
naming — that it answers, that the follow-up is traceable, that the runtime
accepts what it emits — never the identity of a dependency.

**2. Ask the second question, which instance 3 shows the first one misses:**

> *Is this even executing the code in this checkout?*

A hardcoded `Path.home()` or absolute path inside a test is a silent
wrong-artefact bug. Resolve in this order, and make the override explicit:

```
$EXPLICIT_ENV_VAR  →  <repo>/<artefact>  →  ~/<external-install>
```

Prefer the checkout; keep the home fallback so existing setups still run; expose
the env override so CI can be unambiguous. Note the carve-out below — resolving
from home is *correct* when the external install genuinely is the subject.

**2-bis. For shape 3 specifically, read the assertion as the parser does.**
An `or`, a trailing `or True`, a bare `assert some_function` (truthy function
object, never called), `assert x == x`, or a comparison whose both sides derive
from the same expression are all tautologies that survive a reading-for-intent
review. The mechanical tell is that the assertion contains a subexpression that
is constant-true; the habit is to ask *what value of the program state makes
this line fail?* — and if there is none, it is shape 3.

**3. Prove the test can fail, before trusting that it passes.** Run it against
the pre-fix behaviour and watch it go red. A check that has never been observed
failing is an oracle, not a test, and its green tells you nothing. This is the
single highest-value habit in this rule: it caught instance 3 and validated the
fixes for 3, 4 and the gate that closed HAL #516.

**4. When a test demands the defect, invert it — never delete it.** Deletion
discards the coverage and the intent. Inversion keeps both and records, in the
diff, that the expectation was wrong. Preserve the original *intent* where it was
sound: instance 4's "do not raise on a corrupt cache" was right; only the
sentinel changed.

**5. Seed fixtures from real output, not invented shapes.** `_VALID` in HAL's
`test_build_happi_windows.py` carried `"04-python-runtime.py": "x = 1"` — a
minimal shape no build could ever produce, which is why it diverged from reality
and later failed a legitimate new check. When it did, the **fixture** was
corrected, not the check weakened.

## Carve-out — do NOT flag these

Over-applying this rule is its own failure. Measured on GRIP's suite:
`Path.home()` and friends appear **326 times across 160 test files**, and nearly
all are correct — GRIP's home directory genuinely *is* the artefact under test,
and skipping when a sibling repo (HAL, GRASP) is not installed is right. A
mechanical gate on that pattern would be roughly **97% false positives**.

So: **no mechanical shim for shape 2 in GRIP.** The functor's N=2 threshold
licenses a shim only where one would actually be sound; here the measurement
refutes it. Legitimate and not to be flagged:

- A test whose subject genuinely is an external install (`~/.hal/config/...`),
  with a skip when that install is absent.
- `skipif` on a genuinely optional dependency or platform (`plutil` on macOS).
- Fixtures that hardcode a path *they themselves create* under `tmp_path`.

The reviewable signal is narrower than the pattern: an artefact resolved from
`$HOME` **when the same artefact also exists in the checkout**. That is a
judgement a reader makes, not a regex.

## Falsification

Wrong if, over the next ~20 test changes: inverting rather than deleting a
defect-asserting test proves to cost more than it saves (then allow deletion when
the intent is genuinely obsolete); OR the prove-it-can-fail step never once
surfaces an oracle (then it is ceremony — but it has caught 3 of 5 instances so
far, so this is currently refuted); OR a mechanical shim for shape 2 is built
elsewhere and runs below a 10% false-positive rate (then the no-shim conclusion
was GRIP-specific, as this rule already states, and should be narrowed rather
than dropped).

Track: tests found asserting a defect per quarter (target 0 new); green suites
later found to have never executed the code under change (target 0).

## Scope

Every test in every repo. Sits beside `r0-critical-thinking.md` (the
falsification habit this applies to test design), `verify-canonical-not-claimed.md`
(a green suite is a claim, not canonical state), `no-stubs.md` (the same
integrity axis, applied to implementations rather than assertions), and
`knowledge-maturation-functor.md` (which promoted this at N=5 while its own
measurement withheld the shim). Mirror into `~/.hal/rules/`.
