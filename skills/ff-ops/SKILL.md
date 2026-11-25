---
name: ff-ops
description: Control the FF For Future FinancialDataRegistry contract on Sepolia via one-line bash invocations. Wraps the Alchemy CLI. Use to deploy the contract, publish/update records, read records, watch live events, replay record history, simulate transactions, and check status. The kernel skill for any FF-related blockchain op.
---

# /ff-ops — FF FinancialDataRegistry Control Surface

One-line bash invocations for every FF op. Wraps `alchemy --json --no-interactive` via `lib/ff_ops.py` (≤120 LOC kernel, CC ≤4 per function).

**Plan**: `plans/ff-grip-alchemy-control-2026-05-24.md` (broly mesh council 2026-05-24).
**Composes**: Alchemy CLI + lib/precog (deferred IDR) + lib/kono (deferred events) + lib/scrollback_lru (deferred cache) + lib/fold_compress (deferred event-log fold).

## When to Use

- Deploying the FF contract (post-faucet)
- Publishing a borrower data record or AI-IDR record to-chain
- Reading a record back (verify on-chain state)
- Watching the live event stream
- Replaying a record's full version history (audit chain walk)
- Simulating a transaction before broadcasting (gas + revert preview)

## What It Does

Every subcommand below = ONE bash line. Stdout is human-readable; full JSON detail lands under `~/.claude/state/ff-ops/<tx-hash>.json` for `call` ops. Record IDs derived via keccak-256(label) — Ethereum's standard, so they match the on-chain recordId the contract computes via Solidity `keccak256`.

### Implemented (shipped in PR #3234 + PR #3236)

| Subcommand | Form | What it does |
|---|---|---|
| `status` | `ff-ops status <contract>` | Composite snapshot: address, record-count summary, state-dir |
| `read` | `ff-ops read <contract> <method> [args…]` | View call — `getRecord`, `getCID`, `getAllRecordIds`, etc. |
| `call` | `ff-ops call <contract> <method> [args…]` | State-changing tx — `publishRecord`, `updateRecord`, etc. |
| `watch` | `ff-ops watch <contract> [--from-block N]` | Event log stream via `eth_getLogs` |

### Planned (not yet implemented — follow-up PRs)

| Subcommand | Why deferred |
|---|---|
| `deploy` (one-shot bytecode deploy) | Needs encrypted-keystore signer wiring (R0 callout #1 below) |
| `publish` / `cid` / `list` (high-level label-keyed helpers) | Thin convenience wrappers over `call/read`; ship when V>> uses them ≥3× |
| `replay` (IDR audit-chain walk per record) | Awaits chain into the IDR audit substrate |
| `simulate` (eth_call gas/revert preview) | YAGNI until first real gas-revert surprise |

### Record IDs: keccak-256 (Ethereum standard — matches on-chain)

`lib.ff_ops.record_id(label)` computes `keccak-256(label.encode("utf-8"))` via `Crypto.Hash.keccak` (pycryptodome — already a venv dependency), so the id MATCHES the on-chain recordId the FinancialDataRegistry derives in Solidity via `keccak256(bytes(label))`, AND matches any `ethers/web3 keccak256(label)` caller. This is the Ethereum standard — NOT NIST SHA3-256, which differs by the padding byte (keccak 0x01 vs SHA3 0x06) and yields a different digest for every input. Fixed in the keccak-honesty PR; prior tooling used `hashlib.sha3_256`, which silently disagreed with every Ethereum `keccak256` caller (the recordIds never matched the chain).

## Plain-Language Examples

```bash
# Deploy once (after Sepolia faucet drop to dev wallet)
ff-ops deploy --bytecode build/FinancialDataRegistry.bin

# Publish a record (Label resolves to deterministic recordId via keccak)
ff-ops publish "GreenBond-2026-Q1" "QmXyzCIDFromIPFS..."

# Read it back (full record envelope)
ff-ops read "GreenBond-2026-Q1"

# Just the CID — pipeable to ipfs cat
ff-ops cid "GreenBond-2026-Q1" | xargs -I{} ipfs cat {}

# List every record label seen on-chain
ff-ops list

# Watch new events live (drops to KONO on each event)
ff-ops watch

# Replay one record's full version history
ff-ops replay 0xabc123...

# Simulate before broadcasting (catches reverts before paying gas)
ff-ops simulate publish "NewRecord" "QmTestCID"

# Quick state snapshot
ff-ops status
```

## How V>> Tells GRIP Things

- "publish the Thornfields Q1 record with CID QmXyz" → `ff-ops publish "thornfields-2026-q1" "QmXyz"`
- "what's on-chain right now" → `ff-ops list`
- "watch for new events" → `ff-ops watch` (long-running)
- "is the demo state right?" → `ff-ops status`
- "what's the CID for the Claude IDR" → `ff-ops cid "idr:thornfields-2026-q1:claude"`

## R0 Honest Callouts (forwarded from plan §9)

1. Alchemy CLI `--account` signer model not yet verified against our encrypted keystore.
2. agentic-gateway x402 wallet auth may collide with deployer keystore.
3. Webhook HMAC plumbing undefined — needs `grip-alchemy-webhook` keychain entry.
4. `alchemy evm logs` streaming vs polling behaviour uncertain.
5. `getRecord` LRU may serve stale data if `RecordUpdated` event missed (mitigated by TTL ≤60s).

Each is a pre-registered hypothesis falsifier in `lib/hypothesis_engine.py`.

## Hypotheses (pre-registered)

- **H-FF-OPS-1**: `ff-ops publish` end-to-end p95 ≤30s (deadline 2026-06-15)
- **H-FF-OPS-2**: `lib/ff_ops.py` stays ≤120 LOC over first 5 feature requests (deadline 2026-07-01)
- **H-FF-OPS-3**: RecordPublished → KONO latency p95 ≤60s (deadline 2026-06-22)
- **H-FF-OPS-4**: Goodhart-proof test suite catches ≥80% of seeded mutations (deadline 2026-06-08)
- **H-FF-OPS-5**: V>> issues ≥3 distinct `/ff-ops <subcommand>` per FF-active session over 5 sessions (deadline 2026-06-15)

## Companion Skills

- `/ff-watch` — long-running event watcher (RecordPublished → KONO via Banach fold)
- `/ff-replay` — IDR audit chain walk for a record's version history

## Self-Falsification

This skill is wrong if ≥2 of the 6 plan §10 conditions trigger. Fallback: direct foundry `cast` + custom Python wrappers.
