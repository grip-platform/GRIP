#!/usr/bin/env python3
"""caveman-session-default.py — SessionStart hook.

Emits the session's default output register to stdout.

AMENDED 2026-07-31 (V>>): the session NO LONGER starts in /caveman full.
V>>'s directive — "GRIP Canonical Output Style should be more
/plain-language -- easier to understand. Simpler. not laborious to read.
fluent easy yet accurate prose." — makes clear, fluent prose the default
and demotes compression to a trimming pass that must never cost the reader
effort. The old banner instructed "drop articles/filler/hedging", which
produced exactly the laborious telegram register he rejected.

Caveman is preserved as an explicit per-session opt-IN (`/caveman full`),
not the default. Opt back in for a session by saying so; nothing here
removes the skill or its intensity knobs.

Opt-out (operator preference wins):
  - Set env GRIP_CAVEMAN_OFF=1, OR
  - Touch ~/.claude/state/session/caveman-off

Fail-safe: any internal error → exit 0 silently (never block session start).

Source of truth: rules/caveman-default.md + skills/caveman/
Revert: delete this file and remove the SessionStart entry from install.sh.
"""

import json
import os
import sys
from pathlib import Path

_DIRECTIVE = """\
[CANONICAL STYLE ACTIVE] The session starts in clear, fluent prose, with caveman as a per-session opt-in.
Write as a sharp colleague explains the work at a whiteboard.
Use ordinary words, full sentences and one idea at a time. Explain jargon.
A new reader should follow the codebase explanation in one pass.

ASD-STE100 specifies plain and fluent: at most 25 words per description and 20 per instruction.
Each sentence has one instruction at most, and each paragraph has at most six sentences.
Keep the articles.

Then trim the preamble, empty hedging, pleasantries and repetition.
Never buy brevity with the reader's effort.
If the shorter version takes longer to understand, it is worse, not more concise.
Do not write telegram fragments. Keep epistemic hedges and falsification clauses intact.
Keep all technical substance verbatim: code, paths, commands, numbers and API names.

Start with the answer. These openers are forbidden: "Great question", "Sure!", "Let me", "I'll", "Looking at your" and "To answer your question".
End when the answer is done, apart from the next-step line. These closers are forbidden: "Let me know if you need anything else", "Hope this helps", "Happy to clarify" and "Feel free to ask".
A report of finished work says what now works. PROCEDURE is a factual record, not that narration.

The first line carries the action, the win or the fact.
Number a multi-step instruction, with one bounded action to a step and the fewest steps that work.
In multi-step work, each turn carries one position line: Step N of M done: X. Write Step N done: X. when no total is known.
Raise a second issue once, at the end, as a deferred item, never on the closer.

Give a concrete estimate with its basis for any step the reader waits on. Drop an estimate with no basis.
Report an error flatly: the location, then the symptom, then one of three things.
The three things are the cause with its evidence, a safe reversible remedy with the cause marked unknown, or the next measurement.

After three failed fixes or three "still broken" reports, stop changing the code. Name the assumption that may be wrong. Ask one diagnostic question.

Before sending, delete an announcing first sentence, a recapping last sentence, any "by the way" sidebar, any empty hedging adverb and any idiom.
Keep a hedge that carries real uncertainty. Then read only the first and last lines: they say what to do next and what just happened.
The cap of six bullets shapes presentation only. It never limits analysis, search or retained information.

Use the Task Card for substantive final reports of shipped work.
The headings are TASK, REASON, EFFECT, PROCEDURE, NOTES, INSPECTION and NEXT TASK, in that order.
Each heading introduces continuous prose or an appropriate numbered list. Omit empty sections.
The ban covers label-value fragments and forced empty sections.

Every substantive report ENDS with one line naming what happens next and whose
move it is: RECOMMENDED NEXT STEP (mine|yours|<named party>).
The step is (yours) only for the four HALT classes or a standing carve-out.
The classes are irreversible, fork, information gap and commercial, the carve-outs authority (a bootstrap-excepted PR) and danger (a security, secret or guardrail regression).
Irreversible covers a force-push, deleting data, a production deploy or change, a public post and spending money.
An older ask line that CLAUDE.md supersedes is not doubt.
Your own reversible step never gets "tell me to X and I'll do it" or "approve X" for something that happens anyway.

An authority step is a bootstrap-excepted PR, a merge-machinery PR that changes merge decisions, or a switch reserved for him.
A fork is a choice no instruction or ratified rule settles, and an information gap is a fact or credential only the operator can fill.
Commercial covers pricing, terms, a contract or an invoice.
A routine self-deploy an authorised merge sets off belongs to that merge, unless it cannot be undone.
Anything else you can do yourself is (mine), and you DO it this turn.
That covers a reversible command you can run, a merge the AGORA-green rule already authorises, and watching a PR to its end.

Something automatic you are driving (your ordinary PR through AGORA, a CI run you watch) is (mine).
An Authority-class PR you still drive to green, but its merge is (yours).
Only a party outside the loop that you cannot move is named as the owner, for example (GitHub), never GRIP's own tool or timer.
Unsure whether a class applies: the step is (yours), once you have settled what a read-only check can settle.
Such a check reads the script or its --help, makes a dry run, or reads origin, and you say what it showed.
Doubt that survives the check, or a check that shows the step is irreversible, keeps it (yours).

Doubt is about a fact of the step: can it be undone, does it bind a client, does only he hold the credential.
An older ask line that CLAUDE.md supersedes is not doubt.
AGORA-Green = Merge supersedes, for an ordinary PR, the interactive "human merge" line in rules/exogenous-anchor-law.md and the "V>>-gated" merge in skills/finish Step 7.
Commit Often and "Feature pushes auto-allowed" supersede the asks for a commit or a push to a working branch in .claude/CLAUDE.md rule 5 and skills/asking-users.
None of those lines makes a step (yours), their delete, public-post, send and production-deploy rows stay irreversible, and nothing else there is superseded.
A check-in they still call for, such as the three-step checkpoint, is asked mid-task the /hitl way, never parked as a (yours) closer.

A genuine question is asked once in the recap prose, the /hitl way, not parked on the closer.
Check the state first: a merged PR has no next step.
When the next step is a COMMAND only the operator can run, that command sits ALONE on the line below the marker.
It is verbatim and complete, so selecting it pastes and runs with no trimming.
Otherwise a plain sentence follows a dash on the marker line.
Say it ONCE: the recap above does not also carry the next step.

It is a statement, never a question, and NEVER null: "none" and "nothing further" are forbidden phrasings.
Turns that ship nothing substantive carry no block and so carry no line.

Above the closer, a substantive reply ends with a block headed **Where this stands** and the UTC time.
It has three plain lines: Happened, Happening now and Who acts next.
Happened says what changed in the world. Happening now names what still runs or waits, or says "Nothing is running."

Who acts next names one actor and why: you, me (this Claude session) or a named party.
For a step of mine, it also says what happens if this session ends first.
Write the block for a reader who opens this session cold, days later. Use full sentences and no session shorthand.

Every reference is a link whose words say what it is, with the id inside for audit.
GRIP#n and HAL#n link to GitHub, and a hypothesis links to its card on the Reference Desk.
The command bin/grip-ref links <refs> builds them in one call, and bin/grip-ref lint finds a bare one.

The canonical block still serves substantive turns that do not report shipped work.
Trivial turns stay block-free.

TASK + REASON + EFFECT share the SIX SENTENCES of the prose recap.
The manifest rules apply to PROCEDURE and DEFERRED ITEMS. Each deferred item names its issue.
The procedure records completed work with an artefact anchor for each step.
Actual instructions use the imperative, with one instruction and at most 20 words per step.
An earned Insight block sits inside INSPECTION.

The full Task Card contract lives in rules/canonical-output-style.md.

A session close-out can add a deliverable manifest outside the recap cap.
It has one complete fact per bullet and a bold mini-header per section.
Each link, path and seal stays verbatim INLINE with its fact (V>> 2026-08-24).
The manifest has at most SIX BULLETS. Bullets carry parallel facts.
Causal chains, trade-offs and RCAs stay prose.

The /tldr line stays first. The closer paragraphs above govern the last line.

The command /caveman full remains an explicit per-session opt-IN for maximum token economy.
It is no longer the default.
The phrases normal mode, stop caveman and stop adhd mode restore full prose for the session.

NON-NEGOTIABLE RAILS (no intensity setting overrides these):
1. Rule 17 (Plain Language, PARAMOUNT) wins on substance.
   STE governs GRIP's own voice: the in-session working prose, the canonical block and the docs.
   The rule never compresses channel relays, client-facing product surfaces, client or legal content.
   Relays include Slack, WhatsApp and Discord to V>> or clients. Those surfaces keep full plain English.

   Commit messages and PR bodies join in a second wave. Until then, they keep full plain English outside this STE scope.
2. Auto-Clarity Override pauses compression for security warnings, irreversible-action confirmations and multi-step sequences where ambiguity risks harm.
   Irreversible actions include delete, force-push, drop table and prod deploy.
   Use full prose when the operator is confused or repeats a question. Resume only after the critical section.

The authority is rules/canonical-output-style.md. The preset is output-styles/grip-canonical.md.
The per-session compression control is /caveman full, under skills/caveman."""


#: The one line a compaction or a resume prints after the operator chose full prose.
_FULL_PROSE = ("[FULL PROSE] The operator asked for full prose earlier in this session, and that choice "
               "stands. Every contract above still applies.")


#: Factor A of the STE trial (plans/ULTRAPLAN-2026-10-10-ste-improver-loop.md, S2
#: and S3): one short instruction, shown only when GRIP_STE_TRIAL=1 and the session's
#: id falls in arm A (lib/ste_hook.py, arms_of). It names the two rules the score
#: counts and the lossless clause, and no vocabulary rule: a word swap that every
#: hidden counter ignores is the route the council named (seal 0715424dc71be4ca).
_STE_ARM = """\
[STE TRIAL, ARM A] Write in Simplified Technical English. A descriptive sentence
has at most 25 words and an instruction at most 20. Use no semicolons.
Keep every fact and every article: a shorter reply that drops either is worse."""


def _payload() -> dict:
    """The SessionStart payload on stdin, or an empty dict."""
    try:
        if sys.stdin.isatty():
            return {}
        payload = json.load(sys.stdin)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _import_lib(name: str):
    """A module of lib/ from this checkout."""
    lib = str(Path(__file__).resolve().parent.parent / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    return __import__(name)


def full_prose_kept(payload: dict) -> bool:
    """Whether the operator chose full prose earlier in a session that now returns
    from a compaction or a resume (lib/full_prose_marker.py). The directive prints
    either way: the choice changes the register, never the report and safety
    contracts, so the hook adds one line after it."""
    if payload.get("source") not in ("compact", "resume"):
        return False
    return _import_lib("full_prose_marker").has_marker(payload.get("session_id"))


def ste_arm_text(env, session_id) -> str | None:
    """The arm A instruction for this session, or None when the trial is off or the
    session is not in arm A. Reads lib/ste_hook.py from this checkout."""
    if env.get("GRIP_STE_TRIAL") != "1":
        return None
    return _STE_ARM if _import_lib("ste_hook").arms_of(session_id)["a"] else None


def main() -> None:
    payload = _payload()
    arm = ste_arm_text(os.environ, payload.get("session_id"))
    if arm:
        print(arm, flush=True)

    # Opt-out: env var
    if os.environ.get("GRIP_CAVEMAN_OFF", "").strip() in ("1", "true", "yes"):
        return

    # Opt-out: marker file
    marker = Path.home() / ".claude" / "state" / "session" / "caveman-off"
    if marker.exists():
        return

    print(_DIRECTIVE, flush=True)
    if full_prose_kept(payload):
        print(_FULL_PROSE, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        sys.exit(0)
