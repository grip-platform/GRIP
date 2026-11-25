# /verify-embeddings

Prove GRIP's semantic memory works end to end — that embeddings are correctly
**saved** and **loaded/retrieved**. It embeds a probe, stores it, and retrieves
it by similarity, all against a throwaway database (your real memory is never
touched), then reports the result in plain English.

## Usage

```bash
/verify-embeddings
```

## What it checks

- ollama is reachable and the `nomic-embed-text` model answers
- a probe can be embedded, stored, and returned as the top match (save + retrieve)
- the embedding is stable (cosine ~1.0)

## Implementation

```bash
PYTHONPATH="${CLAUDE_DIR:-$HOME/.claude}" "${CLAUDE_DIR:-$HOME/.claude}/venv/bin/python" \
  -c 'import json,sys; from lib.embeddings_warm import verify_roundtrip; r=verify_roundtrip(); print(json.dumps(r, indent=2)); sys.exit(0 if r["ok"] else 1)'
```

On success it prints the cosine and confirms retrieval. On failure the message
names the fix — usually `grip embeddings-warm` or `ollama pull nomic-embed-text`.
It never recommends the retired `lembed` backend.

Related: `grip embeddings-warm` (turn on always-warm semantic memory),
`/grip-doctor` (overall health).
