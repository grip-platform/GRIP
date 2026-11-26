# Libraries

Core libraries and utilities for GRIP.

## Contents

| File | Purpose |
|------|---------|
| `mode-loader.sh` | Mode activation and management |
| `session-start.sh` | Session initialization |
| `instance-serializer.py` | Instance state serialization |
| `improvement-analyzer.py` | RSI heatmap analysis |
| `learning-detector.py` | Pattern learning detection |
| `orchestrator.sh` | Session orchestration |
| `embeddings/` | Semantic embeddings package |
| `session-control-server.py` | WebSocket server for session monitoring |
| `session-monitor.py` | Monitor client library + CLI |
| `tailscale.py` | Tailscale VPN helper |
| `mcp_app_generator.py` | MCP Apps data generator (10 generators) |
| `project_health.py` | Project health metrics and MCP apps |
| `converge_metrics.py` | Convergence loop metrics recording and compound analysis |
| `vision_engine.py` | Multimodal vision LLM engine (Llama 3.2 Vision 11B) |
| `pdf_extractor.py` | PDF page-to-image extraction (PyMuPDF, generator pattern) |
| `pdf_batch_processor.py` | Batch PDF text extraction controller (vision-llm/tesseract/API) |
| `pdf_benchmark.py` | Benchmark recording and cross-method comparison (JSONL) |
| `cc_feature_scanner.py` | CC release feature scanner — scores features for GRIP auto-harnessing |
| `cc_auto_harness.py` | CC Auto-Harness convergence runner — fractal feature integration engine |
| `cc_rate_limiter.py` | CC rate limit detection and adaptive backoff for RSI throttling |
| `cc_identity_resolver.py` | CC org/account env var resolution with identity.md fallback |
| `operator_signature.py` | The operator's signature for a commit's Primary Author line, in the order `rules/commit-standards.md` names (session identity, `facts/identity.md`, git config name); `strict=True` never falls back to a real name, for public or pseudonymous commits (`bin/grip-operator-signature [--strict]`) |
| `cc_task_metrics.py` | Task completion metrics capture and genome fitness updates |
| `floorplan.py` | Deterministic scaled floor-plan rendering — dimensioned SVG with scale bar |
| `artifact_accuracy.py` | Accuracy instruments (rect/mask IoU, RMSE, SSIM, pixel accuracy) |
| `radar/` | grip-radar: the economical reply/GRASP-activity monitor (deterministic diffs, WhatsApp lines, no LLM) |
| `web_fetch_harness.py` | Reads a public web page through another harness (`bin/grip-web-fetch <url> ["question"]`, `rules/fetch-through-another-harness.md`): Codex on the subscription, locked to web search in an empty folder, with the request through the redaction shield. It reads Codex's JSON events, refuses a URL that looks private, and withholds the answer when the ladder did not report the subscription, when the run shows any event kind outside its allowlist, or when no completed event shows this page opened. A timeout stops the whole process group. Its output is labelled a summary, never verbatim source text. `private_host` is public: `untrusted_egress.py` tells a local address from a web one with it. |
| `untrusted_egress.py` | What a pending Bash command sends out (GRIP#8959): splits it into the simple commands the shell would run (operators, redirections, `bash -c`, `eval`, wrappers such as `rtk`, `sudo`, `env` and `timeout`) and classifies each send as HOLD (outside the trusted owners: an upload with curl, wget or HTTPie, a raw socket, a remote copy or shell, mail, a gh write to another owner's repository or a gist, a git push elsewhere) or NOTE (a git push to a trusted remote, a gh write to a trusted repository, a GraphQL mutation, a request with data in a long address). The trusted owners are gh's signed-in accounts (`hosts.yml`) and the checkout's origin owner, so no name is written in the code. A push target keeps the host only, never a login. |
| `untrusted_turn.py` | What entered the turn since the operator's last message (the transcript record with origin kind "human"; a task notification is not the operator): WebFetch, WebSearch, browser pages, fetches to public addresses, gh reads of another owner's repository, and reads of files a fetch wrote, traced across the last 16 MB of the transcript. `judge(payload)` joins this with `untrusted_egress.py` into a verdict for `hooks/untrusted-egress-gate.py`: a note by default, a hold under `GRIP_EGRESS_GATE=hold`, a record only under `off`; `audit_record` keeps kinds, hosts, repositories and `would_hold`, never a path or a command. |
| `fleet_watch.py` | The fleet watch: transcript growth per tick, repeated tool errors, headless harness processes; verdict QUIET or ACT, never stops anything (`bin/grip-fleet-watch`) |
| `tick_cost.py` | What a recurring prompt costs inside a session: per-tick cached input, output and wall time from the transcript, attributed through the `parentUuid` chain up to the record that ends the turn, synthetic rows excluded, unfinished ticks counted apart, with optional USD rates for a single-model transcript |
| `grip_tick.py` | The always-on tick (`bin/grip-tick`, launchd `com.grip.tick` every 300 s): a deterministic floor with no model, checking the fleet watch, unanswered HITL questions, overdue hypotheses, failed launchd agents and weekly spend steps, reporting each new finding once on WhatsApp and never stopping anything it did not start; each line of `logs/grip-tick.jsonl` is one tick's JSON record with `at`, `seconds`, `findings`, `new`, `errors`, `overran`, `sent` and `verdict` (QUIET, ACT, ERROR or OVERRUN) |
| `tick_grant.py` | The operator's grant for one autonomous act of the tick (`bin/grip-tick-grant init|issue|show|revoke`): an Ed25519 signing key in a keychain item with no trusted app, typed to `security(1)` through a pseudo-terminal so it never reaches argv, and every read of it prompts the user; the verifying key as PUBLISHED on main (fetched through `gh api` hourly, cached in `pin_cache`, kept across outages, nothing verifies until one fetch succeeds), with the checkout's `config/tick/grant-verifying.pub` (committed only by the operator's own PR) reported when it drifts; a grant binds act, triggers, limits, issue and expiry times, a nonce and the key fingerprint; `verify()` answers one of ok, malformed, unknown act, key mismatch, bad signature, not yet valid, expired or no verifying key pinned; `load_grants()` keeps one valid grant per act. `grip_tick.grant_findings` reports every grant that fails as a finding, logs every valid one in `grants_seen`, and the daily digest lists the grants in force. The acts it knows and the limit each grant binds: `checkpoint` (`max_mb`, trigger `fleet:hot`/`fleet:stuck`), `throttle` (`soft_cap_pct`, trigger `spend:cap`) and `experiment` (`experiments`, each approved hypothesis id bound to its manifest's SHA-256, trigger `hyp:overdue`; `issue experiment --hypothesis <id>`, repeatable, checks each drafted manifest against the files and prints its argv before the keychain asks, and `--hypothesis` with any other act is refused). `show` names the hypotheses an experiment grant approves. |
| `tick_checkpoint.py` | The tick's first granted act (`bin/grip-tick-checkpoint list|remove`): on a new hot or stuck fleet incident whose kind the verified `checkpoint` grant names, with no checkpoint yet for the incident and room under the grant's cap, copy the transcript through its last complete record into `state/grip-tick/checkpoints/<owner>-<stamp>-<key8>/` with offset, byte count and SHA-256 in `index.json`, check the folder's final size against the cap before any seal, then seal the act on the tick's own ledgers (`state/grip-tick/acts.idr.jsonl`, a `tick-act-checkpoint` leaf, and `acts.context.jsonl`, the paired memory node), which never move the installation's main decision head; a seal that fails, a short copy or a breached cap removes the folder and the act did not happen. Eligibility is by incident identity (a new hot hour or a new error on a known transcript is a new checkpoint), and two valid grants for one act shut the gate. `grip_tick._act` runs it from `run()` before `split_new` and delivery, never on a dry run, and only while the tick has used less than `ACT_BUDGET_SHARE` (a constant in this file) of its budget: a slow tick with an act waiting defers it to its next run and says so once a day, and with nothing waiting says nothing; the act's own finding (`act:checkpoint:*`, `act:cap:*`, `act:error:*`) goes out with the tick's report. `remove` deletes one folder directly under the root. |
| `tick_throttle.py` | The tick's second granted act: the throttle flag `state/session/throttle.json`, raised when an organization's weekly Claude spend (the `seven_day_utilization` the tick already reads from HAL's cache-loss ledger) is at or over the `throttle` grant's `soft_cap` (0.8 by V>>'s decision of 2026-10-29), under a grant naming the `spend:cap` trigger, on a tick that measured the spend, and only when no flag is set; the raise is sealed on the tick's act ledgers as a `tick-act-throttle` leaf and a paired memory node and the leaf's id is written into the flag, a flag that cannot be sealed is removed, and a flag of the tick's own with no leaf (a kill between the write and the seal) is dropped by the next tick and reported. The tick clears its own flag, with a second sealed leaf, once a measurement shows every organization below the cap (the grant's, or the flag's own once the grant has expired, so an expired grant never strands a flag); a missing or unreadable ledger neither raises nor clears; a flag any other writer put there is never touched, and deleting the file is the reversal. The launchers read the flag through `throttle_flag.py` (next row), which this module re-exports. `grip_tick._act` runs it behind the same half-budget gate as the checkpoint act; its findings are `act:throttle:<raised-at>`, `act:throttle-cleared:<at>`, `act:throttle-unsealed:<at>` and `act:error:throttle:*`, one per transition. `bin/grip-tick-grant issue throttle --soft-cap 0.8` signs the grant, a whole percent from 1 to 99. |
| `throttle_flag.py` | The throttle flag as a launcher reads it, and nothing else: `flag_path()`, `read_flag()`, `is_set()` (true while the file exists, readable or not), `describe()` (one line naming the spend, the cap and the remedy, or the fail-closed line for an unreadable or malformed flag, which still throttles) and a command line (`describe`, `is-set`). It reaches no record writer, so a shebanged tool may import it without the re-exec `tests/test_pq_reexec.py` demands and the shell's `python3` may run it. By V>>'s decision of 2026-10-29 a headless GRIP-started run refuses to start while the flag is set and an interactive session prints one warning line: `bin/grip`'s `grip_handoff` (`_grip_throttle_gate`, through `python3 lib/throttle_flag.py describe`, exit 2 for a `--print`/`-p`/`--bg`/`--cloud` launch), `headless_run.run_headless_task` (a `denied` receipt before any harness) and `relay_dispatcher.run_claude` (`THROTTLED_EXIT`, the reason delivered by rung 3; the opencode harness is not refused). Since GRIP#8844 four more spawn sites call `refuse_if_set()`, which raises `Throttled` with `describe()`'s line: `harness.cli.ClaudeCodeHarness.as_argv` (so a Claude guest inside another host is refused too; `run()` turns the raise into an honest failure), `worktree_swarm_coordinator` (a Claude agent only, before any worktree and again before each atom's agent, so a flag raised mid-swarm stops the rest with exit 75; the result's `error` carries the line), `bin/grip_judgement_run.py` (exit `THROTTLED_EXIT`, 75, as the relay gives) and `sovereign_council`'s Claude seat (a dead speaker; the other seats answer). A run on a non-Anthropic model spends no Claude limit and is not refused. Deleting the file is the reversal. |
| `tick_experiment.py` | The tick's third granted act (`bin/grip-tick-experiment draft|list|remove`): for a hypothesis the tick's `hypotheses` check finds pending past its deadline, run once the measurement the operator approved, and put what it printed beside the hypothesis for the operator's verdict; it never writes a hypothesis or its status. The approval is a manifest, `state/grip-tick/experiments/<id>.json` (exact argv, a timeout of at most `MAX_TIMEOUT_S`, the SHA-256 of every input the approval covers, and `live`, the files declared as data: `draft` pins every argv element that names a file, a script handed to an interpreter included, plus any `--input`, unless it is declared `--live`, such as a log, and always pins the executable, a bare name such as `rg` resolved on `SAFE_PATH` and written into the argv as the path found, so an update that replaces it authorises nothing; `validate` refuses an executable left to a PATH search, unpinned or live); an `experiment` grant binds each id to its manifest's SHA-256, so an edited manifest or a changed script authorises nothing and is one `act:experiment-drift:<id>:<grant>` finding; an id the hypothesis store holds in two distinct records (`hypothesis_engine.divergent_ids`) never runs and is one `act:experiment-ambiguous:<id>:<grant>` finding, checked when the tick chooses and again just before the spawn; the inputs are checked once more just before the spawn, and so is the grant's window (`tick_grant.in_force`, a lapse is one `act:experiment-lapsed:*` finding). The run: under `/usr/bin/sandbox-exec` with the network, Apple Events and every write denied except inside the run folder's `out/`, the GRIP tree as working folder, an environment of PATH, HOME, the locale, TMPDIR (inside `out/`), CLAUDE_DIR and PYTHONDONTWRITEBYTECODE only, and no file it writes past `MAX_OUTPUT_BYTES`; `out/` is measured at every poll, and past `MAX_RUN_BYTES` every file together or `MAX_RUN_ENTRIES` files and folders the group is killed and the result says `over_quota`; its whole process group is killed when its leader ends or its timeout passes, before the leader is reaped, so no background child outlives it; no sandbox, no run. The tick reads both streams back through its own descriptors and writes its own copies, the manifest and the signed grant into the run folder above `out/` (which the command cannot write, so it cannot plant a link there), each through a temporary file and an atomic rename, then removes `out/` with `force_remove` (owner access restored and user flags cleared, in passes, so a folder the command locked still goes, and a run whose scratch cannot be removed does not complete). `result.json` is written marked `reserved` before any seal, the once-only marker, then sealed on the tick's act ledgers through `tick_seal.py` (a `tick-act-experiment` leaf and a memory node) and rewritten marked `sealed`; a run that fails before its reservation, or whose seal fails cleanly (`SealFailed`), is removed and runs again, a run whose leaf may already be on the ledger (`SealUncertain`) is kept with `record: uncertain`, and a run stopped between its reservation and its record (a kill, a full disk) stays `reserved`, never runs again, gets no engine row and is one `act:experiment-unsettled:<run>` finding after `INCOMPLETE_S` (`unsettled`). Then one row in `learning/hypothesis_experiments.jsonl` through `hypothesis_engine.record_experiment` (which refuses an id that names two different records and skips a run it already holds), retried by later ticks while `result.json` says it is owed; a marker that cannot be written after the row is one retryable error, never an exception. One run per hypothesis per manifest per grant (`run_key`), read from the run folders; at most one run a tick, last, and only while the tick is inside `grip_tick.ACT_BUDGET_SHARE` after the other acts; a failure while choosing is one `act:error:experiment:choice:*` finding and never takes the tick down. Findings: `act:experiment:<run>` (exit, milliseconds, byte count and SHA-256 of the output, never the output), `act:experiment-drift:*`, `act:experiment-ambiguous:*`, `act:experiment-unsettled:*`, `act:experiment-engine:*`, `act:error:experiment:*`. `remove` deletes one run folder, the reversal, after which the hypothesis may run once more under the same grant. |
| `tick_seal.py` | The seal a granted act of the tick is recorded with: `seal_act(act, decision, inputs, summary, ...)` writes a `tick-act-<act>` decision leaf on `state/grip-tick/acts.idr.jsonl` and a paired memory node on `acts.context.jsonl`; when the leaf is written and its node is not, a roll-back leaf naming it follows and `SealFailed` is raised, so the ledger never claims an incomplete act; when that roll-back leaf cannot be written either, `SealUncertain` (a `SealFailed` carrying `.leaf`) tells the caller the ledger may claim the act, so the caller keeps the act's work instead of repeating it. Nothing sealed may hold a float (`jcs.py`). The experiment act uses it; the checkpoint and throttle acts still carry their own copies (GRIP#8854). |

## Key Libraries

### mode-loader.sh

```bash
source lib/mode-loader.sh
list_modes
activate_mode "code"
mode_status
```

### instance-serializer.py

```bash
python3 lib/instance_serializer.py auto --achievement "Session work"
python3 lib/instance_serializer.py list
```

### improvement-analyzer.py

```bash
python3 lib/improvement_analyzer.py summary
python3 lib/improvement_analyzer.py heatmap
python3 lib/improvement_analyzer.py check
```

### session-control-server.py / session-monitor.py

```bash
python3 lib/session_monitor.py list
python3 lib/session_monitor.py connect SESSION_ID --events Bash Write Edit
python3 lib/session_monitor.py connect SESSION_ID --steer
```

### mcp_app_generator.py

```bash
python3 lib/mcp_app_generator.py sprint_kanban      # Kanban board from GitHub
python3 lib/mcp_app_generator.py project_circuit    # Circuit dashboard
python3 lib/mcp_app_generator.py backlog_table      # Prioritized backlog table
python3 lib/mcp_app_generator.py sprint_timeline    # Git/PR timeline
python3 lib/mcp_app_generator.py pr_rubric          # PR review rubric
python3 lib/mcp_app_generator.py retrospective      # Sprint retrospective
python3 lib/mcp_app_generator.py idea_canvas        # Idea quadrants
python3 lib/mcp_app_generator.py gap_visualiser     # Skill coverage gaps
```

Available generators: sprint_kanban, project_circuit, backlog_table, sprint_timeline, pr_rubric, retrospective, idea_canvas, gap_visualiser, progress_tracker, digest

### pdf_batch_processor.py

```bash
# Extract text from a PDF using local vision LLM
python3 -m lib.pdf_batch_processor --method vision-llm --input doc.pdf

# Extract using Tesseract OCR
python3 -m lib.pdf_batch_processor --method tesseract --input doc.pdf

# Process entire directory
python3 -m lib.pdf_batch_processor --method vision-llm --input corpus/

# Compare benchmark results
python3 -m lib.pdf_batch_processor --benchmark
```

## Dependencies

Most libraries use standard Python/Bash. The core packages every install gets
are listed in `requirements-core.txt` and checked by
`<venv-python> lib/core_requirements.py check-imports` (GRIP#9399): `apsw` and
`sqlite-vec` for KONO semantic memory, `pyyaml`, `cryptography>=48` for the
record seal, `rich` and `websockets`. Optional dependencies, in `requirements.txt`:
- `sentence-transformers` for embedding model
- `llama-cpp-python` for local LLM inference (vision + text)
- `PyMuPDF` for PDF page rendering
- `pytesseract` + Tesseract for OCR baseline
- `anthropic` for Claude API extraction
