# Cross-Harness Background Monitor

Origin: 2026-09-29. During an overnight AFK loop on OpenCode, PR #6777 was held
on a GitHub Actions runner outage and the agent only polled `gh pr checks`
on-demand, once per turn. The operator's check — "are you running a background
monitor?" — surfaced the gap: Claude Code has native background monitors
(Workflow / agent teams) that keep watching a PR between turns; OpenCode and
other harnesses do not. Without an explicit poller, an agent waiting on AGORA —
or any GitHub Action — stalls forever between turns.

## The rule

On any harness WITHOUT a built-in background monitor (OpenCode, and others),
whenever you are waiting on AGORA or any GitHub Action — e.g. merge-on-green —
run an explicit background monitor. Never rely on on-demand polling between
turns: "re-poll on the next check" is stalling, not monitoring.

Use `bin/grip-pr-monitor <PR> --subject "…"`, launched detached, which:

- polls every `--poll` seconds up to a `--ttl` bound (default 8h);
- reads the AGORA verdict comment and HOLDS (never auto-merges) on
  `REQUEST_CHANGES`;
- holds while a GitHub Actions job on the PR's head is red, unless main's newest
  finished run of that job is red too (V>> 2026-10-15, `red_check_hold`): GitHub
  refuses a merge only for a red REQUIRED check, and GRIP#8230 merged past its
  own red sweep, which left four tests red on main for six hours;
- holds while a GitHub Actions run on the PR's head has not finished (V>>
  2026-10-26, GRIP#8682, `pending_check_hold`): GitHub waits only for the
  required checks, so GRIP#8678 merged while a slower job was still running,
  and that job then failed on main. A cancelled run has finished, so cancelling
  a run that can never finish releases the hold;
- then merges `--squash`, which GitHub still refuses while a required check is red;
- never force-pushes and never `--admin`-merges past a red required check.

`bin/grip-pr-monitor <PR> --auto` delegates the watch to GitHub auto-merge —
robust to session end / reboot — but is verdict-unaware (it merges on
required-checks-green regardless of the AGORA comment or of any non-required
check, red or still running), so prefer the poller unless robustness matters
more than the REQUEST_CHANGES guard.

The verdict-aware hold is doctrine-complete, not REQUEST_CHANGES-only:
`hold_reason()` holds on **REQUEST_CHANGES**, **NO_QUORUM**, and **absence of any
AGORA verdict** — absence-of-signal is not a PASS (`exogenous-anchor-law.md`),
so an unrated PR never merges silently. DEFER_CHANGES is not blocking (the
sweep merges it, matching the AGORA-green rule).

## Durable monitor — `--sweep` + scheduler (PARAMOUNT)

`nohup … &` from the bash tool is NOT a durable monitor in OpenCode: the
process is reaped when the tool session tears down, so a long CI run leaves the
PR unwatched. It worked twice (PRs #6874/#6891) only because both merge windows
were under ~2 minutes. Do not trust `ps -p <pid>` alone — the falsifier is
`pgrep grip-pr-monitor`, and a `nohup`-spawned monitor that has been reaped
passes `ps -p` once and fails forever after.

The durable shape is `bin/grip-pr-monitor --sweep`, a one-shot, idempotent,
verdict-aware pass that discovers the authenticated user's open GRIP PRs and
merges-or-holds each, then exits 0. A scheduler re-fires it; a killed fire costs
nothing and the next resumes from live GitHub state.

- macOS: `launchd/com.grip.pr-monitor.plist.template` (StartInterval 300s).
- Linux/VPS: `config/pr-monitor.{service,timer}` (systemd --user oneshot, 5-min
  timer) or the equivalent cron line in the service's header.

**VPS governance** is the right end-state — the Hetzner VPS is always-on where
the Mac sleeps. The single gating item is a merge-capable GitHub credential on
the VPS (`gh` installed + authed, or `GH_TOKEN` with `pull_requests: write` and
`contents: write` on grip-platform/GRIP); it currently holds only read-only
`git fetch` for relay self-deploy. Until that lands, run the sweep from the Mac
launchd agent; migrate fully to the VPS once `gh auth login` succeeds there.

## Why

The merge gate is exogenous (CI-green + AGORA). An agent that acts only on turns
has no watcher; without one, ready work sits unmerged and unreported for as long
as CI takes, and the operator cannot tell monitoring from a stalled loop.

## Falsification

This rule is right iff a non-Claude-Code session waiting on AGORA has either a
live poller (`pgrep grip-pr-monitor`), a GitHub auto-merge in place, or a
scheduler (`launchctl list | grep pr-monitor`, or `systemctl --user status
pr-monitor.timer`) firing `--sweep` — and does NOT answer "still blocked, will
re-check" with no watcher actually running.

## Scope

Cross-harness. Claude Code harnesses have native monitors and may use them; this
rule is for the harnesses that do not. It changes nothing about the merge anchor
itself (`rules/exogenous-anchor-law.md` stands): the poller waits on the same
green CI + non-REQUEST verdict it would merge on by hand.
