# A GRIP Workflow Is Never Left Stuck and Never Burns Tokens

- When you start a fleet, a loop, a workflow or a background agent, arm a watch for it in the same turn, because work nobody watches is the work that burns the weekly limit: a 273-agent workflow spent 32 points of it on one tweet reply on 2026-10-26.
- Give every loop a stop condition and a tick budget before it runs, because a loop without an end burns at the moment nobody is watching.
- While anything you started still runs, run `bin/grip-fleet-watch` at least every ten minutes and read its verdict, because a stuck loop and a token burn look the same from outside until transcript growth and repeated tool errors are measured.
- When the watch says ACT, stop the work you started before the next tick (TaskStop, CronDelete, or the loop's own stop), because every further tick spends the limit on the same failure.
- Never stop a process you did not start; report it by session id and pid instead, because on 2026-10-11 a pattern kill took five other sessions' viewers.
- State the watch's last verdict in every report on fleet or loop work, and leave the verdicts where the watch writes them, one JSON line per tick in `logs/fleet-watch.jsonl`, because a watch nobody reports is theatre and a quoted verdict needs one place where it can be checked.

Narrative (origin, incidents, measurements): `docs/doctrine/workflows-never-stuck-never-burning.md`.
