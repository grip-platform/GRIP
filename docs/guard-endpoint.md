# GRIP Guard + Analyse endpoint

A small, keyed HTTP service that exposes three GRIP safety checks and one
analysis path. Built for the AI Craftspeople Guild demo (task #256) so a
partner can test GRIP's safety substrate against their own text — behind a key,
not a public URL.

## What it is — and what it is not

**Be precise about the claim.** GRIP does **not** ship a general
"prompt-injection detector", and this endpoint does not pretend to be one.
What it exposes are three real GRIP checks plus a provider-agnostic analysis
path. **The signed receipt names which checks ran, so an ALLOW attests "none of
these fired" — never a blanket "safe".**

| Endpoint | What it does |
|----------|--------------|
| `POST /guard` | Runs three checks and returns **ALLOW** or **DENY**: (1) GRIP's guardrail-evasion detector — flags content that has been altered to hide a credential from a plain code review; (2) a sensitive-data scan — flags secrets, API keys, private-key blocks, email addresses, and IP addresses; (3) an L1 prompt-injection + dangerous-instruction floor — flags shapes like "ignore previous instructions", `rm -rf`, `curl \| sh`. The third check is a **deterministic pattern floor, not exhaustive** — base64 / homoglyph / paraphrase evade it, and an L2 semantic layer is future work — so an ALLOW means "none of these three fired", **not** "safe to run". |
| `POST /analyse` | Sends the text through GRIP's provider-agnostic HAL layer for a summarise / draft / classify answer. The provider (any of several) is chosen by GRIP; the caller never picks one. |
| `GET /health` | Liveness. No key required. Reports the service name and whether a key is enforced. |

## The three properties that make it the moat, not a toy

The thing GRIP sells is **keyed, fail-closed, receipted** safety. This endpoint
is delivered by a keyed, fail-closed, receipted service — it practises what it
sells:

1. **Keyed.** Every `POST` must carry `Authorization: Bearer <key>`. The key is
   compared in constant time. A caller gets a key, never a bare URL.
2. **Fail-closed.** If any check cannot complete, the verdict is **DENY** — the
   endpoint never returns ALLOW when it could not finish checking. `/analyse`
   is fail-**safe**: if the analysis backend is down it returns
   `{"ok": false}` (HTTP 503), never a made-up answer.
3. **Receipted (scoped).** Every reply carries a tamper-evident receipt: an
   HMAC-SHA256 signature over the content hash + verdict + **the list of checks
   that ran** + timestamp. Because the checks are inside the signed body, an
   ALLOW attests "none of *these* fired", never a blanket "safe" — GRIP will not
   cryptographically vouch for coverage it did not perform. A caller can prove
   exactly what GRIP returned, and cannot forge a different receipt: change one
   character of the reply and the signature no longer matches.

## Running it

```bash
# Loopback, no key — local development only
grip-guard-serve

# Require a Bearer key on every POST (production / shared)
printf '%s' "$(openssl rand -hex 24)" > ~/.grip-guard.token
chmod 600 ~/.grip-guard.token
grip-guard-serve --token-file ~/.grip-guard.token --port 8642
```

The default bind is `127.0.0.1:8642` (loopback). To let an external partner
reach it, front it with the existing cloudflared tunnel rather than binding to
all interfaces — that keeps GRIP's auth posture at the public edge — and always
set `--token-file`.

The signing key for receipts resolves in this order: the
`GRIP_GUARD_SIGNING_KEY` environment variable, then the macOS keychain entry
`grip-guard-signing-key`, then a per-install key generated once and stored at
`state/guard-endpoint/signing.key` (mode 600). Receipts stay verifiable across
restarts by default.

## Calling it

```bash
KEY=$(cat ~/.grip-guard.token)

# Guard: is this text safe to send to a model?
curl -s -X POST http://127.0.0.1:8642/guard \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"text": "please summarise this contract"}'
# -> {"ok":true,"verdict":"ALLOW","signals":[],"content_sha256":"…","receipt":{…,"sig":"…"}}

# A message carrying a secret is denied, with the reason shown:
curl -s -X POST http://127.0.0.1:8642/guard \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"text": "my key is sk-EXAMPLE0000000000000000test"}'
# -> {"ok":true,"verdict":"DENY","signals":[{"kind":"secret-token",…}],…}

# Analyse: summarise / draft / classify via GRIP's provider-agnostic layer
curl -s -X POST http://127.0.0.1:8642/analyse \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"text": "long text here", "task": "give me a two-line summary"}'
# -> {"ok":true,"result":"…","receipt":{…,"sig":"…"}}
```

Without a valid key, every `POST` returns `401 unauthorized`. A request with no
`text` field returns `400`. An unknown path returns `404`.

## One-shot (no server), for a quick local demo

```bash
python3 lib/guard_endpoint.py guard  "please review this NDA"
python3 lib/guard_endpoint.py analyse "long text" --task "two-line summary"
```

## Where the pieces live

| File | Role |
|------|------|
| `lib/guard_endpoint.py` | The endpoint (guard, analyse, receipt signing, HTTP surface). Standard-library only. |
| `lib/guardrail_evasion_detector.py` | The shipped guardrail-evasion check the `guard` path runs. |
| `lib/hal_llm_adapter.py` | GRIP's provider-agnostic AI layer the `analyse` path routes through. |
| `bin/grip-guard-serve` | The runner. |
| `tests/test_guard_endpoint.py` | Goodhart-resistant tests (fail-closed, auth, tamper-evidence, real-socket wire contract). |
