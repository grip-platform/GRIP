# A GRIP Workflow Is Never Left Stuck and Never Burns Tokens: annex

The narrative behind `rules/workflows-never-stuck-never-burning.md`: its origin, incidents and measurements. The rule holds the operative text; nothing here loads at startup.

## Origin

V>>, 2026-10-28, through `/remember`, after a session that ran a ten-minute fleet watch beside its main work: *"now that all of these GRIP workflows must never be stuck and no tokens should be burning -- incorporate as part of /efficient -- rename the skill/command."* The skill is now `skills/efficiency` (`/efficient` redirects to it) and the watch is its section E6. Rule-at-N=1 under `knowledge-maturation-functor.md` because the operator declared it, with the shim shipped in the same change because the failure is silent by construction: a burning fleet looks, from the outside, exactly like a working one.

## The incidents the law is written against

- **2026-10-26, the 273-agent Workflow.** One tweet reply fanned out to 273 agents with no agent cap, Pool-A judges at effort max over roughly 237K-token prompts, and a 4.5-hour retry stall nobody watched. It spent 32 points of the weekly limit (`memory/feedback_workflow_fanout_burned_32_points_of_the_weekly_limit.md`).
- **2026-10-26, GRIP#8714.** A conflicting PR gets no pull_request workflow runs at all, so AGORA never sat and the wait looked like a billing block for 2.5 hours (`memory/reference_conflicting_pr_gets_no_workflow_runs.md`). Stuck, not burning, and invisible until measured.
- **2026-10-11, the pattern kill.** A `pkill -f`-style cleanup aimed at one `claude --cloud` viewer killed five processes that were probably other sessions' viewers (`memory/feedback_kill_only_processes_you_started.md`). This is why the law lets you stop only what you started.
- **2026-10-27, GRIP#8715.** A held PR lost its owner when its session died, because the monitor was a child of the session and its kill notice landed in a dead transcript (`memory/reference_a_held_pr_loses_its_owner_when_the_session_dies.md`).

## The measurement that shaped the shim

On 2026-10-27, between 22:13 and 23:11 UTC, a session ran the watch by hand every ten minutes while drafting two contracts. Ten interactive sessions were alive the whole time; nine never wrote a byte. The one working session's transcript grew 3.7 MB in its busiest tick and 45 KB in its quietest, with no repeated tool error. No headless harness process ran. The two signals that separate a burn from work are therefore transcript growth per tick and repeated identical tool errors in the tail, and both are readable without a model call. That is what `lib/fleet_watch.py` measures. "Readable without a model call" says what the two signals cost to measure. The falsification clause below asks a different question: whether they are the right two signals, and a burn that neither of them shows is the case that would refute the law.

## The shim

- `lib/fleet_watch.py` -- one tick: every transcript (session and subagent) written inside the window, its growth since the previous tick, a HOT flag above a threshold (5 MB by default), a STUCK flag when the same tool error repeats five times in the tail of a growing transcript, and every headless harness process. Verdict `QUIET` or `ACT`. It never stops anything. It keeps the baseline it measures growth against per session (`state/fleet-watch/sizes-<session>.json`), so two sessions ticking at the same moment never overwrite each other's and read a false QUIET, and it appends one JSON line per tick to `logs/fleet-watch.jsonl`. It reads Claude Code's transcript layout (`projects/<slug>/<session>.jsonl` and `projects/<slug>/<session>/subagents/agent-*.jsonl`); the test pins that layout, so a change in it fails there first, which is the honest answer to the coupling the watch accepts.
- `bin/grip-fleet-watch` -- the shim; exit 0 quiet, 1 act, 2 no projects root.
- `tests/test_fleet_watch.py` -- the growth, hot, stuck, window, attribution, headless, per-session baseline, tick-log and exit-code cases, each on a synthetic tree under a temporary folder. Nothing in it reads the live `$CLAUDE_DIR/projects`.
- `agents/efficiency-meta-agent.md` -- the agent that runs the watch and reports, by session id and pid, what to stop and who may stop it.
- `skills/efficiency/SKILL.md` section E6 -- the operating procedure.

## Falsification

Wrong if, over the next ~20 fleets, loops or workflows: the watch never once reports ACT on a run that was in fact burning or stuck, while the ten-minute ticks cost more than the burns they would have caught (then narrow the law to fleets over a size threshold, never drop it); or a stop at the first ACT kills work that was legitimately heavy (then raise the HOT threshold or require two consecutive ACT ticks, never drop the stop); or the "only what you started" clause leaves a burning process nobody can stop (then add a named escalation to the operator, never a pattern kill). Track: ACT verdicts per week and what each one led to, from the watch's own output quoted in session reports, and points of the weekly limit spent by runs nobody watched (target 0).

## Siblings

`efficient-always-active.md` (the same law on the token side), `fan-out-spends-the-weekly-limit.md`, `dispatch-economics.md`, `loop-engineering.md`, `cross-harness-background-monitor.md`, `rate-limit-never-blocks.md`, and the memory records named above.
