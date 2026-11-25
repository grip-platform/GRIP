---
name: verify-embeddings
description: "Verify GRIP semantic memory end to end — prove embeddings are correctly saved and loaded/retrieved via a store->retrieve round-trip against a throwaway database. Use when checking embeddings health, after enabling always-warm embeddings (grip embeddings-warm), or when semantic recall seems off."
---

# /verify-embeddings

Run the semantic-memory round-trip proof and report it plainly.

```bash
PYTHONPATH="${CLAUDE_DIR:-$HOME/.claude}" "${CLAUDE_DIR:-$HOME/.claude}/venv/bin/python" \
  -c 'import json,sys; from lib.embeddings_warm import verify_roundtrip; r=verify_roundtrip(); print(json.dumps(r, indent=2)); sys.exit(0 if r["ok"] else 1)'
```

Interpret the result for the operator in plain language:

- `ok: true` — semantic memory works: the stored probe was retrieved as the top
  match and the embedding is stable (report the cosine).
- `ok: false` — relay the `message` field verbatim; it contains the fix (start
  ollama, pull `nomic-embed-text`, or run `grip embeddings-warm`). Never suggest
  the retired `lembed` backend.

The check uses a throwaway temporary database, so it never alters real memory.
