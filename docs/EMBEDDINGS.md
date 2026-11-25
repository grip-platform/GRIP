# Semantic Embeddings

GRIP semantic search using local vector embeddings.

## Quick Start

```bash
./scripts/embeddings-init.sh
```

This script:
1. Checks Python dependencies (apsw, sqlite-vec, sqlite-lembed)
2. Downloads embedding model if needed (~550MB)
3. Initialises database schema
4. Indexes all skills, modes, and commands

## Architecture

| Component | Value |
|-----------|-------|
| Model | Nomic Embed v1.5 (768-dim) |
| Storage | SQLite + sqlite-vec |
| Search | Hybrid RRF (60% vector + 40% keyword) |
| Recency | 7-day half-life decay |

### Hybrid Search (Cortex-inspired)

Search combines vector similarity and FTS5 keyword matching using Reciprocal Rank Fusion:

```
RRF_score = (0.6 / (60 + vector_rank)) + (0.4 / (60 + keyword_rank))
```

This provides better recall than vector-only search, especially for exact terms.

## Usage

### Direct Python API

```python
from embeddings import EmbeddingEngine

engine = EmbeddingEngine()
engine.init_schema()

# Hybrid search (default)
results = engine.search_hybrid("authentication code", limit=5)

# Vector-only search
results = engine.search_similar("authentication code", limit=5)

# Store custom embedding
engine.store_embedding(
    text="Custom concept description",
    embed_type="concept_custom",
    metadata={"name": "my-concept"}
)
```

### CLI Commands

```bash
# Initialize schema
python3 lib/embeddings.py init

# Search (hybrid mode)
python3 lib/embeddings.py search "code review" --mode hybrid

# Search (vector only)
python3 lib/embeddings.py search "code review" --mode vector

# Show stats
python3 lib/embeddings.py stats

# Calculate novelty
python3 lib/embeddings.py novelty "some text"
```

### Skill Indexer

```bash
# Index all skills, modes, commands
python3 lib/skill_indexer.py index

# Search indexed concepts
python3 lib/skill_indexer.py search "code review"

# Search specific type
python3 lib/skill_indexer.py search "sec" --type concept_mode

# Show index stats
python3 lib/skill_indexer.py stats
```

## Embedding Types

| Type | Description |
|------|-------------|
| `concept_skill` | GRIP skills (from skills/*/SKILL.md) |
| `concept_mode` | Operating modes (from modes/definitions/*.yaml) |
| `concept_command` | Slash commands |
| `concept_agent` | Agent definitions |
| `prompt` | User prompts (auto-embedded) |
| `checkpoint` | Session checkpoints |
| `plan` | Plan documents |

## Integration Points

### Mode Semantic Matching

When `/mode sec` doesn't find exact match, semantic fallback tries:

```bash
python3 -c "
from skill_indexer import SkillIndexer
indexer = SkillIndexer()
results = indexer.search('sec', concept_type='concept_mode', limit=1)
if results:
    print(results[0]['metadata']['name'])
"
```

### Skill Discovery

```python
from embeddings import EmbeddingEngine

engine = EmbeddingEngine()
engine.init_schema()

# Match user intent to skill
results = engine.match_skill("help me write tests")
if results:
    suggested_skill = results[0]["name"]
```

## Files

| File | Purpose |
|------|---------|
| `lib/embeddings.py` | Core embedding engine |
| `lib/skill_indexer.py` | Skill/mode/command indexer |
| `scripts/embeddings-init.sh` | Bootstrap script |
| `embeddings.db` | SQLite database (auto-created) |
| `models/nomic-embed-text-v1.5.Q8_0.gguf` | Embedding model |

## Dependencies

```bash
pip install apsw sqlite-vec sqlite-lembed
```

Or use the GRIP virtual environment:

```bash
source ~/.claude/venv/bin/activate
```

## Database Schema

### embeddings table

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| hash | TEXT | SHA256 hash of content |
| vector | BLOB | 768-dim embedding |
| type | TEXT | Embedding type |
| content | TEXT | Original content (truncated) |
| metadata | JSON | Additional metadata |
| success_score | REAL | Learning score (0.0-1.0) |
| novelty_score | REAL | Uniqueness score |
| created_at | TEXT | Timestamp |

### vec_embeddings (virtual table)

SQLite-vec virtual table for fast vector similarity search.

### embeddings_fts (virtual table)

FTS5 virtual table for keyword search, kept in sync via triggers.

## Acknowledgments

Hybrid search architecture inspired by [Cortex](https://github.com/hjertefolger/cortex) by Tomas Hjertefolger.

---

**
