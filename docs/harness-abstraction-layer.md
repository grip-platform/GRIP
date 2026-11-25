# The Harness Abstraction Layer — outbound dispatch and THE ROOM

**Status:** design, pre-implementation. **Written** 2026-09-16 for a launch build.
**Implementable by:** any harness. This document is the spec; it deliberately
carries enough detail that a cheaper model can build from it without inferring.

---

## 0. What is missing, precisely

GRIP already routes **models** (HAL, Pool A/B/C) and already lets a foreign
harness call **into** its kernel (`lib/ultracode/harness.py`, one HAPPI envelope
in, NDJSON events out, no branch anywhere on which harness is calling).

Nothing goes **outward**. No code spawns another harness and collects its result.
`gg++` launches 18 of them for a human; no workflow can. That asymmetry is the
whole gap, and closing it is what turns "multi-LLM" into "multi-harness".

| Layer | State |
|---|---|
| Multi-LLM routing | exists — HAL |
| Harness → GRIP (inbound) | exists — `lib/ultracode/harness.py` |
| GRIP → harness (outbound) | **missing — this document** |
| Live room + human veto | **missing — this document** |

---

## 1. One adapter, plus an orthogonal control channel

**Revised after adversarial review** (sovereign council seal `0a05ddb3f33744a5`,
speakers `astra` / `deepseek` / `xai` / `groq`, judge `gemini-3.1-pro-preview`,
zero Anthropic). The first draft of this section split the work into two
primitives — a stateless `HarnessAdapter` and a stateful `HarnessParticipant` —
and Astra overruled it. The split was on the wrong axis, and the corrected design
is both simpler and more capable.

**`dispatch(task) -> Any` does not imply statelessness.** A single dispatch may
run an entire *bounded, stateful* session and return its final result. Session
lifecycle and harness-specific protocol translation belong inside the adapter,
which is exactly where the kernel cannot see them. So there is **one**
`HarnessAdapter` behind the existing Protocol, unchanged.

What a result-only interface genuinely cannot do is carry **live** utterances,
human advice, or a veto — a final return value arrives too late to be interrupted.
That is a separate concern and gets a separate seam:

| Seam | Shape | Carries |
|---|---|---|
| `HarnessAdapter.dispatch()` | request → final result | the work, and its outcome |
| **Control channel** | bidirectional event stream | live utterances out; advice, veto and **explicit cancellation** in |

Separating on *concern* rather than on *statefulness* means `/converge-fleet`,
`/dfi-fib` and the councils use the adapter unchanged, while THE ROOM subscribes
to the control channel. Neither has to know about the other.

### 1.1 Three breakages the adapter must handle

Named by Astra; each is a real hazard that a naive implementation walks into.

- **Retry is not free.** Replaying a dispatch can duplicate tool actions already
  taken, or resume contaminated session state. A retry policy must be explicit,
  and a dispatch that performed side effects must not be silently re-run.
- **Process exit is not task completion.** An interactive CLI may block waiting
  for approval or further input rather than exiting. Treating exit as the
  completion signal will either hang the workflow or truncate its result. The
  adapter needs its own completion predicate per harness.
- **Do not assume a CLI speaks HAPPI over stdio.** Most do not. The adapter needs
  a per-harness shim translating the harness's native protocol into HAPPI. This
  shim is the harness-specific code, and it lives in the adapter — never in the
  kernel, where a branch on harness identity would falsify `H-UC-HARNESS`.

---

## 2. `HarnessAdapter` — the one-shot seam

Lives in `lib/ultracode/adapters.py` beside `DeterministicAdapter` and
`HALAdapter`. Same Protocol, same `available()` fail-open convention.

```python
class HarnessAdapter:
    """Routes `dispatch` to a FOREIGN harness CLI in one-shot mode.

    The kernel still imports no harness: this adapter is injected, exactly as
    HALAdapter is. Selection is by registry name, never by a branch on harness
    identity inside the kernel — that branch is H-UC-HARNESS's falsifier.
    """
    def __init__(self, harness: str, *, model: str | None = None,
                 timeout: int = 300, cwd: str | None = None): ...

    def available(self) -> bool:
        """True when the registry knows this harness AND its binary is on PATH."""

    def dispatch(self, task: Any) -> Any:
        """Spawn one-shot, send the task, return the result. Never raises into
        the kernel — a failed spawn returns a structured error result so a
        fan-out is degraded rather than aborted."""
```

### 2.1 The registry needs invocation modes

`lib/grip_harness_picker.HARNESS_REGISTRY` currently records `binary`,
`label`, `blurb`, `install`. It does not record **how to invoke non-interactively**,
which is the one fact outbound dispatch needs. Extend each entry:

```python
"opencode": {
    ...existing keys...,
    "oneshot_argv": ["opencode", "run", "{prompt}"],
    "serve_argv":   ["opencode", "serve"],        # persistent mode, if any
    "attach_argv":  ["opencode", "attach", "{url}"],
    "model_flag":   None,          # provider chosen via HAL gateway :4010
},
```

Verified on this machine 2026-09-16 — all three target peers are installed
(`/opt/homebrew/bin/{opencode,codex,pi}`) and OpenCode publishes exactly the two
modes this design needs:

```
opencode run [message..]     run opencode with a message      -> oneshot
opencode serve               starts a headless opencode server -> persistent
opencode attach <url>        attach to a running server
```

**A harness with no `oneshot_argv` is not dispatchable.** `available()` must
return False for it rather than guessing an invocation — a guessed argv that
happens to open an interactive TUI will hang a workflow with no diagnosis.

### 2.2 Measured invocation surfaces — probed, not assumed

Run 2026-09-17 against the installed binaries, 90s timeout, prompt
`"Reply with exactly: BOOTSTRAP_OK"`. Do not take the argv shapes on trust; two
of three are wrong in ways that cost an implementer an hour each.

| Harness | Probed argv | Result |
|---|---|---|
| **opencode** | `opencode run "<prompt>"` | **exit 0, returned `BOOTSTRAP_OK` exactly.** Dispatchable today, no setup. |
| codex | `codex exec "<prompt>"` | exit 1 — *"Reading additional input from stdin…"*. It wants the prompt on **stdin**, not as an argument. The argv shape above is WRONG. |
| pi | `pi -p "<prompt>"` | exit 1 — *"No API key found for deepseek. Use /login…"*. Auth gap, not a shape problem; argv remains **unverified**. |

So only OpenCode's `oneshot_argv` may be written into the registry as verified.
Codex needs a `stdin` delivery mode — the registry field must express *how the
prompt is delivered*, not merely the argv:

```python
"delivery": "argv" | "stdin",
```

That field is not optional polish. Without it the registry cannot represent Codex
at all, and an implementer working from the argv-only shape will conclude Codex
is broken when it is merely being spoken to incorrectly.

This is also the empirical basis for OpenCode being the primary target: it is the
only one of the three that dispatches non-interactively **today**, with no auth
work and no protocol shim.

### 2.2 Why OpenCode is the primary target

It is the only peer that covers **both** primitives (`run` and `serve`) *and* is
provider-agnostic underneath — its registry blurb records that it routes via
GRIP's HAL gateway on `:4010`. So a single OpenCode peer can represent several
model families, which makes it the strongest single demonstration of the claim.

---

## 2.3 Concurrency: two harnesses in one tree — found by doing

Discovered 2026-09-17 during the first real outbound dispatch, and not predicted
by any of the design above.

While OpenCode was implementing this adapter, a test run against the same
worktree reported **2 failed, 108 passed**. Minutes later, with no fix applied,
the same command reported **133 passed**. The failures were not real: OpenCode was
mid-write, and the tree was briefly inconsistent — one file already saved against
another not yet written.

Nothing was broken, but the correct conclusion was one step from being wrong. The
obvious reading of a red suite is "the other harness broke something", and acting
on it would have meant reverting work that was fine.

**Two harnesses editing one working tree therefore need coordination, and this is
now a first-class requirement of the Room rather than an implementation detail:**

- **A dispatch must publish a working state** — at minimum "writing" versus
  "settled" — so an observer knows whether a red suite means anything. Without it,
  every concurrent verification is a coin flip.
- **`grip-presence` already provides advisory locking** and is already the Room's
  roster. Reuse it: a harness holds a lock on the paths it is editing, and a
  verifier waits for release rather than reading a half-written tree.
- **Prefer isolation where the work allows it.** `lib/grip-worktree.sh` gives each
  session a git-crypt-safe worktree, which removes the race entirely rather than
  managing it. Shared-tree editing should be the exception that justifies itself.

The general lesson, which generalises past this feature: **a red result observed
while another agent is mid-write carries no information.** GRIP already holds this
as doctrine for canonical state (`rules/verify-canonical-not-claimed.md`) and for
matching conditions before attributing a difference; the multi-harness setting is
simply where it bites within minutes rather than days.

---

## 3. The integrity problem, and why it is the actual product

A spawned process can **claim any identity**. If a harness emits "I am GPT-5",
that is a self-report, and `rules/prove-it-claims-canonical.md` holds — under a
binding non-Anthropic council seal — that self-reports are the least reliable
evidence GRIP handles. A transcript of models introducing themselves proves
nothing at all.

**Therefore an utterance must never carry its own identity.**

### 3.1 Dispatcher-signed attribution

The dispatcher is the only party that *knows*, rather than believes:

- the resolved binary path it executed (`/opt/homebrew/bin/opencode`)
- the full argv, including any model flag
- the process start time, exit code and duration
- the bytes it sent and the bytes it received

So the dispatcher emits a **spawn record**, signs it, and binds every utterance
to it by id:

```json
{
  "spawn_id": "sp-<uuid>",
  "harness": "opencode",
  "binary": "/opt/homebrew/bin/opencode",
  "binary_sha256": "<hash of the executable at spawn time>",
  "argv": ["opencode", "run", "..."],
  "model_declared": "<what was requested>",
  "started_at": "...", "exit_code": 0, "duration_ms": 8412,
  "stdin_sha256": "...", "stdout_sha256": "..."
}
```

Each utterance then carries `spawn_id` and nothing self-asserted. Verification is
mechanical: re-hash the recorded I/O, check the chain signature, confirm the
utterance's `spawn_id` resolves to a signed spawn record.

### 3.2 The strongest attack, stated plainly

**Dispatcher-signed attribution proves which BINARY ran. It does not prove which
MODEL served the request.** A harness configured to route to a different backend,
or a gateway that silently fails over, produces a truthful spawn record and a
misleading identity claim. This is not hypothetical — GRIP's own HAL does
provider failover by design.

Do not overclaim. The honest claim is:

> *"These distinct harness binaries were executed with these arguments, and
> produced these exact bytes, and here is the signed chain proving the record
> was not edited afterwards."*

That is strong and it is checkable. **"Four different models deliberated" is not
provable by this mechanism alone** and must not be asserted on the strength of it.

The council sharpened this further: a dispatcher signature proves **that a
particular key endorsed a record and the record was not altered afterwards**. It
does not prove independent execution, and it is *not sufficient against a hostile
verifier who does not trust the dispatcher* — which is the only verifier worth
designing for.

### 3.3 The minimal addition that defeats the replay attack

Signing only the *spawn* leaves the transcript replayable: an adversary could
reuse a genuine signed spawn record and attach fabricated utterances to it. The
fix is to sign **each utterance**, chained:

> On every message received from a harness, append a **monotonic sequence
> number** and a **fresh nonce** — or, better, the **Merkle leaf hash of the
> transcript so far** — and sign that tuple with the dispatcher's key **and** a
> harness-specific key (or an HMAC derived from the spawn record). Append the
> message-signature pair to the immutable log.

The transcript-so-far hash is the stronger nonce choice, because it binds each
utterance to everything said before it. An adversary then cannot replay a spawn
record without reproducing the entire signed message chain, and any edit anywhere
breaks every signature after it.

Use **`sha256` of the executable as the harness's immutable id**, so a swapped
binary is detectable rather than merely asserted.

### 3.4 The residual, which no minimal mechanism closes

Two wrappers around the *same* model produce two different binary hashes. So this
mechanism proves **distinct binaries ran**; it does not prove **distinct models
answered**. Closing that needs provider-side attestation — a TEE measurement or a
provider signature over the model identity — which is real but far beyond a launch
build, and depends on vendors that mostly do not offer it.

State this residual **in the artefact itself**. A receipt that names its own
limits is worth more than one that quietly overreaches, and
`rules/canonical-output-style.md` requires the caveat to travel with the claim
rather than sit behind a link.

### 3.5 The smallest artefact a skeptic can check alone

One self-contained signed Merkle log, on the order of a kilobyte, needing no
service to verify:

```json
{
  "kernel_pubkey": "base64...",
  "residual": "proves distinct BINARIES ran, not distinct MODELS - see 3.4",
  "harnesses": [
    {
      "id": "sha256:/opt/homebrew/bin/opencode",
      "spawn_sig": "base64...",
      "messages": [
        {"seq": 0, "role": "human",   "text": "...", "msg_sig": "base64..."},
        {"seq": 1, "role": "harness", "text": "...", "msg_sig": "base64..."}
      ],
      "merkle_root": "sha256:..."
    }
  ],
  "overall_root": "sha256:..."
}
```

Four steps, all local:

1. Hash each harness binary and confirm it matches its `id`.
2. Verify `spawn_sig` with the kernel public key — binds binary id to spawn event.
3. For each message recompute `hash(seq || role || text || nonce)` and verify
   `msg_sig`.
4. Recompute the Merkle tree to `overall_root` and compare.

If all four pass, the skeptic has cryptographic proof that distinct binaries were
spawned and that every utterance was signed when it was made — **without trusting
GRIP, the dispatcher, or a screenshot.** That is the launch claim, and it is the
largest claim the evidence actually supports.

---

## 4. `HarnessParticipant` — THE ROOM

A stateful peer. Not an Adapter.

```python
class HarnessParticipant:
    def join(self, room: str) -> None: ...
    def observe(self, utterance: Utterance) -> None: ...   # others spoke
    def speak(self, prompt: str) -> Utterance: ...
    def leave(self) -> None: ...
```

Backed by whichever persistent mode the harness has (`opencode serve` +
`attach`), falling back to one-shot-with-replayed-context for harnesses that have
no server mode — a fallback that must be **labelled in the transcript**, because
a peer that re-reads history each turn is not the same thing as a peer that
remembers, and conflating them misrepresents what happened.

### 4.1 Transport and roster — reuse, do not rebuild

- **Transport:** `grip-session-mesh` — WebSocket bus on `:9474`, Tailscale relay
  for cross-machine, already proven.
- **Roster/presence:** `grip-presence` — `session_register`, `session_list`,
  `session_broadcast`, advisory locks.
- **Reach:** `grip-channel` on `:3101` already bridges WhatsApp/Slack/Discord, so
  the human can observe and speak into the room from a phone with no new work.

### 4.2 The human's veto — both mechanisms, split by risk

Matching the split GRIP's HALT classes already encode:

- **Reversible work — pre-announce and wait.** The participant posts its intent
  to the room and waits a bounded interval for objection. Silence proceeds.
- **Irreversible work — hard brake.** Extend `bin/grip-stop`, which already
  exists as a deterministic fail-closed kill switch with a spoken safeword under
  `rules/full-power-stop-override.md`. Widen its scope from outbound sends to
  harness dispatch.

The brake must remain **deterministic and independent of any model's judgement** —
that is the property that makes it a brake rather than a suggestion.

---

## 5. What must be true before this is called done

Per `rules/tests-must-be-able-to-fail.md`, every check below must be observed
failing under a named mutation before its green is trusted.

| Check | Mutation that must turn it red |
|---|---|
| `available()` false for a harness with no `oneshot_argv` | give it a guessed argv → red |
| `available()` false when binary absent from PATH | stub PATH lookup true → red |
| A failed spawn degrades the fan-out, never aborts it | let the exception escape → red |
| Utterance carries no self-asserted identity | add a `model` field the speaker sets → red |
| Utterance resolves to a signed spawn record | break the `spawn_id` link → red |
| Tampering with the transcript fails verification | flip one byte, expect BROKEN → red |
| Kernel contains no branch on harness identity | add `if harness == "opencode"` → red |

That last row is `H-UC-HARNESS`'s own falsifier, restated: if a harness-specific
code path is ever required inside the kernel, the hypothesis is dead and the
abstraction is wrong.

---

## 6. Explicitly out of scope for v1

Named so they are decisions rather than omissions:

- All 18 harnesses. v1 targets **OpenCode**, then Codex, then Pi.
- Proving which *model* served a request (see 3.2 — it is not provable this way).
- Automatic turn-taking or consensus. v1 is dispatch plus transcript; who speaks
  when is the human's or the workflow's call.
- Backfilling existing council seals into room transcripts.

---

## 7. What already exists and must not be rebuilt

Checked on disk 2026-09-16, not assumed:

| Need | Existing |
|---|---|
| Multi-model council, zero Anthropic, sealed + Merkle-rooted | `bin/grip-sovereign-council` — `astra` is in the DEFAULT speaker panel |
| Signing / tamper-evidence | `lib/precog/idr.py`, `lib/idr_forest.py`, `lib/pq_dual_sign.py` |
| Append-before-act signed commitments | `lib/continuity_chain.py` |
| Verifiable receipts | `lib/prove_it.py`, `lib/prove_it_provenance.py` |
| Cross-session/machine transport | `~/CodeTonight/grip-session-mesh` |
| Web surface base | `apps/grip-web` (Vite + React + Tailwind) |
| Harness roster + installer | `lib/grip_harness_picker.py`, `bin/grip-harness-install`, `bin/gg++` |

**A large part of "prove different models talked" already ships.**
`grip-sovereign-council` convenes a multi-provider panel including Astra, seals
each verdict with a verifiable sha, and maintains a Merkle root across all of
them. What it does not do is cover *harnesses* rather than models, and it has no
live interactive surface. That is the delta this document specifies — which is
considerably smaller than building the capability from nothing.

---

## Provenance

Lourens Cornelius "Laurie" Scheepers (V>>)

With assistance of
  Claude Opus 5 (1M context) — `claude-opus-5[1m]`

Design reasoning reviewed adversarially by a non-Anthropic sovereign council —
speakers `astra` (gpt-6-astra), `deepseek`, `xai`, `groq`; judge
`gemini-3.1-pro-preview`, exogenous to the panel. **Zero Anthropic calls.**

    seal   0a05ddb3f33744a5
    record ~/.claude/state/sovereign-council/0a05ddb3f33744a5.json
    verify grip-sovereign-council --verify 0a05ddb3f33744a5

Per `rules/exogenous-anchor-law.md` the verifier is never the author, and the
council did not merely ratify this document — it **overruled** its central design
choice. Section 1 originally split the work into two primitives; Astra showed the
split was on the wrong axis and it was rewritten. Sections 3.3 to 3.5 are the
council's contribution, not the author's: the per-utterance co-signature, the
transcript-so-far hash as nonce, and the one-kilobyte verifiable artefact.

That is the intended shape of this process. A review that changes nothing is
weak evidence that it happened.

Every path, port and invocation flag above was checked against this machine on
2026-09-16. Anything not checked is marked as such or is not claimed.
