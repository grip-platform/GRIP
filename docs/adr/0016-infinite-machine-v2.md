# ADR 0016 -- The InfiniteMachine v2: wire by proxy, real failover, signed records

Date: 2026-10-04 · Status: accepted, 2026-10-04, by V>> · Decided at four brainstorm
gates; plan approved at 17:03 UTC · Supersedes the lost ADR-0002, "HAL InfiniteMachine"
(2026-08-24) · Governing contract: HAL `docs/infinite-machine-contract.md`

**In one line:** HAL becomes a local intercepting proxy, so that no limit or overload
error reaches any harness while allowed capacity is left, Remote Control and voice keep
working, and every call leaves a signed record.

## Context -- what v1 claimed, and what was measured

### What ADR-0002 claimed

ADR-0002, "HAL InfiniteMachine", is dated 2026-08-24. Its headline was "The session
never dies. Provably." Its status read "decisions locked -- build authorised", backed by
broly council `wf_c5d106cd-970`, sovereign seal `e512d12eaa6dfe87` and IDR leaf
`precog-1787525015-b715fb86`. Among its claims:

- **W0** would put Claude Code onto HAL's witnessed rails, by pointing
  `ANTHROPIC_BASE_URL` at the gateway on `127.0.0.1:4010` behind `GRIP_INFINITE=1`. Its
  stated day-one win: Anthropic-tier failover, and witness coverage of interactive
  traffic from 0% to 100%.
- "Same-format Anthropic failover through the relaybill credential pool means provider
  limits stop killing sessions from day one."
- The record would be "witnessed, HMAC-chained, IDR-sealed serving".
- v1 was W0-W3 (decision D1): W1 the never-ending `/clear` cycle, W2 the
  block-preserving thread store, W3 the signed, thread-scoped chain. W4-W6 were a gated
  v2.
- D5: default-on after the probe and a 7-day soak.

### What the 2026-10-04 inspection measured

- **Failover has never fired.** One upstream is configured, and the Max-account pool is
  not connected to the gateway. Since 2026-08-24, 2,546 x 429 and 93 x 529 went
  straight to Claude Code. All 133 "failover" rows in the live log are test fixtures
  written into it, and so are 76 "unreachable" rows.
- **No cost record for Anthropic traffic.** 0 of 205,925 rows are priced; the totals are
  held in memory only.
- **The record is weakly tamper-evident.** It is HMAC-chained, with the key beside the
  log.
- **Loopback only.** If it were exposed as it stands, the guard would admit any
  Tailscale peer without a token.
- **W1 is built but off. W2-W6 are unbuilt.** The 7-day soak review (GRIP#5026) never
  happened. The auth-failover marker latches and fights the credential scrub
  (GRIP#7274).

### The Remote Control conflict

The operator wanted Claude Code's Remote Control on the phone, and voice dictation.
Remote Control never worked. Every `gg++` session is routed through the local HAL
gateway: `~/.zshrc:50` exports `GRIP_INFINITE=1`, and `bin/grip` then sets
`ANTHROPIC_BASE_URL=http://127.0.0.1:4010`. The vendor's rule is that Claude Code
refuses Remote Control for any session whose base URL is not `api.anthropic.com`. An A/B
test on 2026-10-04 measured it: the wired session logged "--rc flag ignored".

The operator's position: HAL's strengths are not worth giving up for Remote Control.
Both must work.

## Decision

The operator made six decisions on 2026-10-04:

1. **Wire by intercepting proxy** for Claude Code: `HTTPS_PROXY`, plus a per-host CA
   name-constrained to `api.anthropic.com`. Never by `ANTHROPIC_BASE_URL`. Other
   harnesses keep base-URL wiring.
2. **"Never dies" covers all of:** account failover; the W1 automatic cycle, gated by an
   `/ultraclear` dry run (clear only when it actually saves something); the W2/W3 signed
   thread store; W4 provider switching through RAILLM.
3. **The promise:** no rate-limit, overload or usage-limit error reaches any harness
   while any authorised capacity exists. One such error is a bug. If literally
   everything is exhausted: one honest pause notice naming when it resumes, then
   automatic resume. Never a silent paid key.
4. **Hosts:** a wire on every trusted host, any OS, plus a central VPS (its own sessions
   and the shared brain). If a host's own wire is down, its new session goes direct,
   with a visible "unwitnessed" badge. Decrypted traffic and each host's CA never leave
   that host.
5. **Records:** public-key signed, with checkpoints held outside the writer's control.
6. **Automatic account switching ships on by default.** The operator decided this at the
   fourth gate, after reading the Consumer Terms clauses quoted under Consequences, and
   accepted the risk to both accounts. One off-switch, the brake, keeps the decision
   reversible in one command.

   *Note, 2026-10-15:* a third Max account, stored under the label `laurdev`, joined the
   pool on this date at the operator's request, so automatic switching now covers three
   accounts. The decision and the brake are unchanged. Each machine keeps its own
   failover order in its pool file (`grip-relay-bill list`).

The build runs in seven steps. Every step ends in tests, and the whole ends in the
operator's manual QA.

1. **Clean evidence, prove the wire, write the contract** (GRIP + HAL).
2. **HAL wire:** a loopback intercepting proxy on `127.0.0.1:4011` (HAL).
3. **The GRIP launcher wires Claude Code by proxy** (GRIP).
4. **The provable thread:** signed records, thread store, cost truth (HAL + GRIP; v1's
   W2 + W3).
5. **Never halt:** hold-open, account failover, provider cascade (HAL; v1's W0 made
   real, plus W4).
6. **Everywhere:** every trusted host, any OS, plus the central VPS (GRIP + HAL).
7. **The never-ending cycle, end-to-end proof, manual QA** (GRIP; v1's W1 + D5).

If any Step 1 case fails, work stops, and the fallback design takes over: a blind tunnel
plus Claude Code's own OpenTelemetry record, with failover off.

The continuity contract governs HAL code. It is HAL `docs/infinite-machine-contract.md`,
and it holds the continuity table, the invariants, the route classes, the
error-to-action table, the thinking-signature rule and the spend guard.

## Consequences

**The intended outcome.** One never-ending session in any harness GRIP launches. HAL
stays in the path everywhere: a proxy for Claude Code, a base URL for the others.
Remote Control and voice keep working. Failover is real, across accounts and then
providers. Every call leaves a public-key signed record.

**What stays impossible.**

- **Claude Code's own login expiry.** Claude Code checks it locally, before any request
  leaves, and asks the user to log in again.
- **A failure after text has streamed.** It shows as one visible, recoverable
  interruption. Two responses are never spliced.
- **A weekly reset more than 24 hours away.** Claude Code's auto-continue does not wait
  for it (scout extraction, unchecked).
- **agy.** It talks to Google directly, with its own login. It stays outside until the
  Step 1 seam probe passes.
- **Codex on the ChatGPT plan (Astra).** HAL is not on that path, so it stays out of
  scope for now.

When every account is spent, the harnesses end differently. Claude Code and OpenCode
should resume by themselves. Codex shows one final message naming the time, with no
auto-resume in the evidence. Hermes, with a reset hours away, ends in one final error
after 3 retries capped at 120 s (scout extraction only).

**The terms risk.** Anthropic's Consumer Terms (effective 8 October 2025, read
2026-10-04) forbid access "through automated or non-human means, whether through a bot,
script, or otherwise" except "via an Anthropic API Key or where we otherwise explicitly
permit it". They also forbid "bypassing any of our systems or protective measures".
Automatic switching between the operator's own Max accounts ships on by default anyway.
That is the operator's decision, made after reading these clauses, with the risk to
both accounts accepted. If Anthropic objects, the brake turns switching off in one
command. Since 2026-10-15 the pool holds three accounts (see the note under decision 6).

**Risks carried.**

- **Anthropic changes the client** (pinning, attestation enforcement). The Step 1
  harness runs on every Claude Code update. On failure, the launcher falls back to
  tunnel or direct, with a visible notice.
- **The intercepting CA.** It is name-constrained, per host, kept in the OS secret
  store, trusted only by GRIP-launched processes, never put in a system trust store, and
  rotated.
- **Silent paid spend by the failover machinery itself.** It has already happened: 8
  hours on the paid key on 2026-10-03, set off by HAL's own 502s. The plan gives billing
  to one fail-closed spend guard (Step 5).
- **HAL as a single point of failure.** Holds raise the stakes, because a gateway
  restart drops every held request at once. Containment: fix HAL's own error sources
  before any hold, cap held requests per host, and fall back to direct with the
  "unwitnessed" badge when the wire is dead.

## Alternatives rejected

1. **Wiring Claude Code by changing `ANTHROPIC_BASE_URL`**, as v1's W0 did. Claude Code
   refuses Remote Control for any base URL other than `api.anthropic.com`.
2. **A blind tunnel without decryption.** Remote Control connects through it, but HAL
   cannot read a response, so it cannot fail over. It is rejected as the design and kept
   only as the Step 1 fallback, with failover off.
3. **Turning the gateway off per session** (`GRIP_INFINITE=0 gg++`, the interim
   workaround). Remote Control works, but the session loses the witness. The operator
   ruled that both must work.

## Evidence

- **The probe of 2026-10-04.** Every proxy was a throwaway Python script
  (`proxy_probe.py`), not HAL: on that date HAL had no CONNECT, TLS or proxy code. Each
  probe ran 35 seconds on Claude Code 2.1.288, with no prompt sent. With `HTTPS_PROXY`
  pointing at the local proxy and `ANTHROPIC_BASE_URL` left at Anthropic's address, all
  23 `api.anthropic.com` connections went through it, and Remote Control reached
  `state=connected`. With the proxy decrypting `api.anthropic.com` only, under a
  throwaway CA name-constrained to that host and trusted via `NODE_EXTRA_CA_CERTS`, the
  proxy saw `POST /v1/messages` (one call, a quota check, no conversation turn),
  `POST /v1/code/sessions`, `.../bridge` and worker traffic. Remote Control reached
  `state=connected`; there was no pinning; the CA was loaded. Proof artefact: a
  `/prove-it` render, 8 citations exact, 1 red by design (IDR
  `sha256:8cfa941e1be470e15...`).
- **The measured cost of interception.** On exit, the interception run failed to archive
  its Remote Control session (`Archive cse_<id> failed: timeout of 599ms exceeded`),
  while the direct and tunnel runs archived cleanly (`archive=200`). Claude Code allows
  under 0.6 s for that call. The extra TLS handshakes of interception plausibly exceeded
  it, leaving an orphaned offline session in the Claude app. Step 2 must make this pass.
- **Not yet measured:** a phone-typed message under interception (device attestation is
  enforced on this account), real streamed conversations, voice through the proxy, proxy
  death mid-session, and Remote Control held open for longer than 35 seconds.
- **Roundtable:** grok-4.5 and gpt-oss-120b, judged by gemini-3.1-pro-preview; seal
  `8c478f2bc0ed0c52`, IDR `precog-1791120334-c4c0c402`.
- **Trio record:** `/ultrathink` `f3f3f1f576b6a281`, IDR `precog-1791120708-3dc9392d`,
  with GPT-6 Astra as lead and DeepSeek v4 Pro as a seat.
- **Converge-fleet run `wf_5f3c7295-40f`:** five expert lanes over Claude Code 2.1.288,
  Codex 0.156.1, OpenCode 1.18.30, Hermes, agy 1.2.8, HAL and the error history; 125
  findings and a 310-line synthesis. Its checker first sent nothing, because of a bug
  that GRIP#7340 fixed (merged 2026-10-04). The re-run checked all 125: 68 coherent,
  35 weak, 22 refuted, none unchecked. The checker tests a finding only against its own
  quote, cannot open the source, and gave different answers to one identical question,
  so a verdict is a hint. Every refutation of a finding that Step 5 relies on was read:
  f2, f54, f59 and f97 reached past their quotes, f52 left out two conditions, and
  f103's quote was cut short. None overturns the design, and the Step 1 stub matrix
  measures all of it before code depends on it.
- **Mesh council** on the fleet synthesis: deepseek-v4-pro and grok-4.5 (both cut off at
  the speaker limit; gpt-oss-120b refused with HTTP 413), judged by
  gemini-3.1-pro-preview; seal `9c8a247fbb7ce8aa`, IDR `precog-1791130871-cc9078d1`. Its
  surviving attacks shaped Step 5's first fixes (stop HAL being the error source), its
  spend guard and its test oracles.
- **Vendor sources,** read 2026-10-04: platform.claude.com "Thinking"; Anthropic's
  Consumer Terms, effective 8 October 2025.

A seal proves integrity, not correctness.

**Where the evidence lives.** The plan and its plain-words explainer are in `plans/`,
which is gitignored. Copies are in `PARAMOUNT_BACKUPS/2026-10-04/`:
`infinite-machine-v2-plan-approved`, `infinite-machine-v2-explainer`,
`infinite-machine-v2-fleet-evidence` and `infinite-machine-v2-probes`.

## What this supersedes, and the number 0002

This ADR supersedes GRIP's ADR-0002, "HAL InfiniteMachine" (2026-08-24). That ADR is in
neither repository today. Its plan lived in `plans/`, which is gitignored, and the
original survives only in `PARAMOUNT_BACKUPS/2026-08-23/`:
`ADR-0002-HAL-InfiniteMachine.pdf`, `ULTRAPLAN-2026-08-24-infinite-machine.md` and
`ULTRAPROMPT-2026-08-24-infinite-machine.md`.

The number 0002 in HAL belongs to RAILLM (`docs/ADR-0002-raillm-ensemble-first.md`). To
end the collision, v2 lives in GRIP as 0016, and HAL carries the contract, not an ADR.
Code comments in both repositories still cite "ADR-0002" for the InfiniteMachine, for
example HAL `lib/hal/gateway_brake.py:1` and `lib/hal/gateway.py:777`, and GRIP
`lib/grip_infinite.py`. Those comments mean the lost ADR, which this one supersedes. In
HAL they never mean the RAILLM ADR.

## Tracking

- GRIP#5026 (ADR-0002 W0 follow-ups: the ambient `ANTHROPIC_API_KEY` audit and D5
  soak-to-default) closes in Step 7, only after manual QA.
- GRIP#7274 (the auth failover never disengages) is retired in Step 3.
- Step 1 pre-registers H-WIRE-1 (interception keeps Remote Control, phone messages,
  streaming and voice), H-HOLD-1 (safe hold duration, per harness and per route) and
  H-INF-SIG (thinking blocks signed under account A accepted on account B). None of the
  three was in `learning/hypotheses.jsonl` when this record was written.

## Provenance

Decided by V>> on 2026-10-04, at four brainstorm gates; plan approved at 17:03 UTC.
Continuity contract and build order drafted by Claude Fable 5.1 (`claude-fable-5-1`).
Main session, probes and synthesis by Claude Opus 5.5 (`claude-opus-5-5[1m]`). Trio:
GPT-6 Astra (`gpt-6-astra`, via Codex) as lead and Roundtable advisor, with DeepSeek v4
Pro. Roundtable: grok-4.5 and gpt-oss-120b, judged by gemini-3.1-pro-preview. HAL
Council: gpt-oss-120b, DeepSeek Chat, Grok 3, Gemini 3.1 Pro and Claude Opus 4.7. Mesh
council: deepseek-v4-pro and grok-4.5, judged by gemini-3.1-pro-preview. An
`/ultrareason` pass ran on HAL Pool B. Record written by Claude Opus 5.5
(`claude-opus-5-5[1m]`).
