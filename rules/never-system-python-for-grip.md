# Never Run GRIP Code Under the System Python (PARAMOUNT)

`lib/pq_reexec.py` probes an interpreter for `mldsa` and `ed25519` and
`os.execv`s into one that can genuinely dual-sign.

## The law

> **Anything that runs GRIP code by path runs under the GRIP venv. `python3` on
> this machine resolves to Homebrew's interpreter, which has no `cryptography`,
> so anything that signs a record writes a forgeable demo seal instead of
> failing.**

## The distinction that actually matters

Not "never type `python3`". The line is whether a **GRIP module** is being run:

| Fine | Not fine |
|---|---|
| `python3 -c "import json"` | `python3 lib/grip_save.py` |
| `python3 -m pytest tests/x.py` | `python3 ~/.claude/lib/prove_it.py render` |
| `python3 /tmp/scratch.py` | `python3 "$CLAUDE_DIR/lib/kono_preload.py"` |

The right-hand column loads GRIP's own modules, which reach `append_idr`. The
left-hand column does not. A rule that banned the word would fire on most Bash
calls in a session and be ignored within an hour, which is worse than no rule.

## Why it is silent, which is the whole problem

Three layers each behaved correctly and the result was still wrong. The record
was **honestly labelled** `custody: demo-shared-key`. The downgrade **was**
announced. The exit code was **0**, because writing a weak record is not an
error. Nothing lied — and a forgeable record was produced where a real one was
one interpreter away. On this installation the demo seal's shared secret is
world-readable and committed.

## What made it recur, and the part that stings

`.github/workflows/forward-anchors.yml` now runs `tests/test_pq_reexec.py`
alongside `tests/test_mythos_is_opt_in_only.py`, on `pull_request` and on every
push to `main`, with no path filter — so the anchor gates every PR.

## Mandate

1. **Type `py`, not `python3`,** for anything under the GRIP tree — or
   `grip-py`, which is the same thing and what scripts should use, since a
   shell function does not exist inside a non-interactive script.
   `bin/grip-py` **refuses** a system interpreter — `resolve_venv_python` falls
   back to one, and silently accepting that fallback is the bug — and separately
   **warns** when the venv it found cannot dual-sign. The two checks have
   different teeth on purpose: a path check is not a capability check, but refusing on
   capability would block `grip-py -m pip install cryptography`, which is the
   remedy. A check must never block its own resolution.

   Its recovery text names `cryptography>=48` explicitly. **GRIP#5646 is CLOSED** (`2026-09-14T02:32:20Z`) and the pin now sits at
   `requirements.txt:79`, so installing from requirements IS the remedy today and
   naming the package separately is belt-and-braces rather than a workaround.

   **The floor is 48, not 47**, and the `>=47` this rule shipped with was wrong
   in the unsafe direction — satisfiable by a version that cannot sign, which is
   the silent downgrade the whole rule exists to prevent. Per the vendor's own
   CHANGELOG, 47.0.0 (2026-04-24) added the `mldsa` module "with AWS-LC and
   BoringSSL" and notes that post-quantum support "requires AWS-LC or
   BoringSSL"; 48.0.0 (2026-05-04) added OpenSSL 3.5.0 support and states that
   post-quantum algorithms are "now available to users of our wheels". Since
   `pip install` delivers those wheels, a resolver landing on 47.0.0 satisfies
   `>=47` and still writes a forgeable demo seal.
2. **Every runnable module that reaches a record writer calls
   `reexec_if_incapable` first,** as the first statement of its `__main__`
   block. That is the net under the habit: even typed wrong, the tool re-execs
   itself into a capable interpreter.
3. **Spawn sites resolve the interpreter**, never `["python3", ...]`. Python:
   `lib/_compat.py::grip_python`. Shell: `resolve_venv_python` from
   `scripts/lib/venv-utils.sh`. Both are cross-platform and both already exist.
4. **A library module with no entry point does not get a shebang.** A
   `#!/usr/bin/env python3` line on a non-executable module claims it is runnable.
5. **Docs teach the safe form.** Nine doc lines were teaching the broken one,
   including a frame rendered into a product video, where no gate inspects pixels
   (`visual-artifact-leak-prevention.md`).

## Enforcement tier (knowledge-maturation-functor)

**Rule now — severe, and N≥2 by measurement, not by anecdote.**

**Mechanical floor, live:** the anchor now walks `lib/` as well as `bin/`
(`tests/test_pq_reexec.py`), 508 shebanged files rather than 91, and its
known-writers guard pins writers in **both** directories so the new half cannot
go vacuous while the old half stays green.

**The habit, live:** `py` is a function in `lib/shell-functions.sh`, which both
rc writers already source, so it reaches every installed machine on its next
shell without any rc being rewritten. It forwards to `bin/grip-py` and
reimplements none of it. Tested under bash and zsh, because unquoting `"$@"`
fails in exactly one of them.

**Advisory, live but unbound:** `lib/system_python_detector.py` +
`hooks/system-python-hook.py` print one stderr line naming the `grip-py`
equivalent. **Binding it in `settings.json` is the operator's step**, and it is
stated here rather than left to be discovered — an unbound hook is decorative,
and this rule does not claim a live gate it does not have.

**The signer refuses, since 2026-10-18.** The one-folder product plan's item A3,
on V>>'s decision 11, inverted the fallback this rule was written around: a record
that needs the dual seal and reaches an interpreter without the crypto backend is
now REFUSED by `lib/precog/idr.py` with the fix and the opt-in named, instead of
being written with the forgeable demo seal and a warning.
`GRIP_IDR_ALLOW_DEMO_WITHOUT_CRYPTO=1` restores the loud fallback for one run. The
other two downgrade reasons (no tenant, unprovisioned tenant) are unchanged. This
is a refusal inside the signer, not a gate on a command, so it cannot block the
command that repairs it (`grip-py -m pip install 'cryptography>=48'` writes no
record); the metric this rule tracks, records written with
`custody: demo-shared-key` for `crypto-backend-unavailable`, measured 28 of 1,562
on the shared chain with the last on 2026-10-01 before the switch, can now only
rise through the opt-in.

**No DENY path exists, and none is planned.** A gate able to refuse a `python3`
invocation could refuse the command that repairs a broken venv —
`gate-resolution-escape-hatch.md` Invariant 1. `tests/test_system_python_detector.py`
asserts against the hook source that no deny path is added later, so a future
edit has to delete that test to do it.

## Falsification

Wrong if, over the next ~20 sessions: no record is written under a system
interpreter even with the advisory unbound (the habit alone suffices — demote to
advisory); OR the detector's accepted false positive — a command that merely
quotes the broken form — fires often enough that the line is ignored (tighten the
pattern, never drop it); OR `grip-py`'s strict refusal blocks legitimate work on
a machine where the venv is intentionally absent (widen to a warned fallback,
never a silent one). Also wrong if the widened walk's ~6.4s cost, up from ~1.1s,
proves to matter in CI more than the eight entry points it found.

Track: records written with `custody: demo-shared-key` whose downgrade reason is
`crypto-backend-unavailable` (target 0), and record-writing
entry points shipping unwired (target 0 — now mechanically caught in both `bin/`
and `lib/`, though only where something actually runs that test file).

## Scope

Every session, every machine, every harness. Sits beside
`tests-must-be-able-to-fail.md` (the anchor's own instance of shape 1),
`verify-canonical-not-claimed.md` (an exit code of 0 is a claim, not evidence),
`cryptographic-causation.md` (an un-signable record is theatre, not evidence),
`gate-resolution-escape-hatch.md` (why this never becomes a DENY), `no-stubs.md`
(the shipped-inert posture named honestly) and `path-portability.md` (both
resolvers are cross-platform). Shim: `bin/grip-py` ·
`lib/system_python_detector.py` · `hooks/system-python-hook.py` ·
`tests/test_grip_py_shim.py` · `tests/test_system_python_detector.py` ·
`tests/test_pq_reexec.py`.

Narrative (origin, incidents, measurements): `docs/doctrine/never-system-python-for-grip.md`.
