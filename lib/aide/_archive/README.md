# Copilot Archive

Archived 2026-05-03 during the M3 "Copilot Resurrection" sprint
(branch `feat/copilot-resurrection`, PR pending).

The fresh-start Copilot is built around five pillars:

1. `adaptive.py` — preference / routine / prediction registry (kept, hardened)
2. `absorb/` — knowledge absorption (gog, slack, whatsapp)
3. `graph/` — entity graph builder, Cytoscape JSON output
4. `brief.py` — morning + afternoon brief generator
5. `precog.py` — precognition layer (proactive context surfacing)

These archived modules pre-date the rebuild. They are kept on disk for
reference (and to make the diff reviewable) but are NOT imported by the
new core. Anything still load-bearing has been ported into the fresh
modules above; if you find a reference back into `_archive/`, it is a
bug.

| Archived module | Disposition |
|-----------------|-------------|
| `adaptive_learning.py` | Superseded by `adaptive.py` (was a parallel re-implementation) |
| `agent_adapter.py` | Replaced by direct shim usage (`lib/slack_transport.py`, gcal CLI) |
| `command_palette.py` | Frontend concern, moved out of backend lib |
| `decision_purge.py` | Pruning is now handled inline by the writer, not a separate purger |
| `delivery_report.py` | Subsumed by `brief.py` |
| `escalation.py` | Trust gates are sufficient; escalation chains were unused |
| `integrations.py` | 64 KB grab-bag — split into per-source absorbers under `absorb/` |
| `outcome_discovery.py` | Predictions now own their own outcome verification (`precog.py`) |
| `ritual_engine.py` | Replaced by launchd plist + a thin scheduler in `brief.py` |
| `routine_predictor.py` | Subsumed by `adaptive.py` routine learning |
| `standup_bot.py` | Subsumed by `brief.py` morning brief |
| `thread_registry.py` | Slack threading is now handled at the transport shim |
| `workflow_composer.py` | Workflow composition not part of MVP scope |
| `workflow_runner.py` | Same — out of MVP scope |
| `workflow_templates.py` | Same — out of MVP scope |

If a future session needs to revive any of these, copy the file out of
`_archive/`, rename, and re-test against current callers. Do not import
from `_archive/` directly.
