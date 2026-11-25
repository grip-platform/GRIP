# Cortex Technical Analysis & GRIP Integration Opportunities

**Date**: 2026-01-26
**Session**: Gen 200
**Source**: [github.com/hjertefolger/cortex](https://github.com/hjertefolger/cortex)
**Author**: Tomas Hjertefolger
**License**: MIT

---

## Executive Summary

Cortex is a memory system for Claude Code that adds persistent, cross-project memory. It solves **memory retrieval** ("what was said") while GRIP solves **session continuity** ("what was decided"). These are complementary approaches.

### Key Stats

- ~6,400 lines TypeScript
- 187+ unit tests
- WASM SQLite (sql.js) for storage
- Nomic Embed v1.5 (768-dim) via ONNX for embeddings
- Hybrid search: vector + FTS5 + recency decay + RRF fusion

---

## Architecture Overview

```
Claude Code Session
    |
Stdin JSON (context, model, working dir)
    |
Command Router (index.ts)
    |-- statusline: Display fragment count + context circles
    |-- session-start: Initialize analytics
    |-- post-tool: Detect context step (5% threshold)
    |-- pre-compact: Auto-archive before /clear
    +-- session-end: Save final state
    |
Archive Module (archive.ts)
    |-- Parse JSONL transcript line-by-line
    |-- Extract messages (user/assistant roles)
    |-- Split into semantic chunks (75-600 chars)
    +-- Weight content (decisions, architecture = higher priority)
    |
Embeddings (embeddings.ts)
    |-- Nomic Embed v1.5 (768-dim, via @xenova/transformers)
    |-- Quantized ONNX runtime (faster inference)
    +-- Prefix-aware: "search_document: {text}" for passages
    |
Database Write (database.ts)
    |-- Deduplication: SHA256(content.trim()) -> 16-char hex
    |-- Atomic write: temp-file + rename (prevents corruption)
    |-- Insert into memories table
    +-- FTS5 triggers auto-sync keyword index
    |
Search Query
    |-- Embed query: "search_query: {text}" prefix
    |-- Parallel search: Vector + Keyword
    |-- RRF fusion: Weight vector (60%) + keyword (40%)
    |-- Recency decay: 7-day half-life exponential
    +-- Return top-5 ranked memories
```

---

## Database Schema

```sql
-- Core memories
CREATE TABLE memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE,  -- SHA256[:16]
  embedding BLOB NOT NULL,            -- Float32Array (768*4 bytes)
  project_id TEXT,
  source_session TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- FTS5 virtual table (auto-synced via triggers)
CREATE VIRTUAL TABLE memories_fts USING fts5(
  content,
  content='memories',
  content_rowid='id'
);

-- Session turns (precise restoration after /clear)
CREATE TABLE session_turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  role TEXT NOT NULL,                 -- 'user' | 'assistant'
  content TEXT NOT NULL,
  project_id TEXT,
  session_id TEXT NOT NULL,
  turn_index INTEGER NOT NULL,
  timestamp TEXT NOT NULL
);

-- Session summaries
CREATE TABLE session_summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT,
  session_id TEXT NOT NULL UNIQUE,
  summary TEXT NOT NULL,
  key_decisions TEXT,
  key_outcomes TEXT,
  blockers TEXT,
  context_at_save INTEGER,
  fragments_saved INTEGER,
  timestamp TEXT NOT NULL
);
```

---

## Techniques Worth Adopting

### 1. Reciprocal Rank Fusion (RRF)

**Algorithm:**
```typescript
const RRF_K = 60;

// For each result from vector search (rank 0, 1, 2, ...):
rrfScore_vector = 0.6 / (60 + rank + 1)

// For each result from keyword search (rank 0, 1, 2, ...):
rrfScore_keyword = 0.4 / (60 + rank + 1)

// Combine by memory ID (same memory in both = summed)
finalScore = rrfScore_vector + rrfScore_keyword
```

**Why it works:**
- More robust than weighted average of raw scores
- Handles heterogeneous score distributions (cosine sim vs BM25)
- No normalisation needed

**GRIP application:** `/remind-yourself` could combine semantic + grep + recency.

---

### 2. Recency Decay with Half-Life

**Formula:**
```typescript
const halfLifeMs = 7 * 24 * 60 * 60 * 1000;  // 7 days
const decayFactor = Math.pow(0.5, ageMs / halfLifeMs);
finalScore = contentScore * (0.7 + 0.3 * decayFactor);
```

**Blend:** 70% content relevance + 30% recency factor

**GRIP application:** Session resurrection scoring, chat history ranking.

---

### 3. Content Deduplication via SHA256

```typescript
const hash = crypto.createHash('sha256')
  .update(content.trim())
  .digest('hex')
  .substring(0, 16);  // 64 bits = collision-resistant
```

**Result:** 10-30% storage savings from duplicate content.

**GRIP application:** Instance snapshots often contain repeated file contents.

---

### 4. Atomic Write Pattern

```typescript
const tempPath = `${dbPath}.tmp.${process.pid}.${Date.now()}`;
fs.writeFileSync(tempPath, buffer);
fs.renameSync(tempPath, dbPath);  // POSIX atomic
```

**Benefit:** Crash between write and rename = temp orphaned, main intact.

**GRIP application:** `instance-serializer.py`, session state persistence.

---

### 5. Context-Step Auto-Save

- Track last saved percentage
- On every tool call, check if `current - lastSaved >= 5`
- Background save (non-blocking)

**GRIP comparison:**
- Current: 80% hard gate (HALT + ask)
- Cortex: 5% soft save (no interrupt)
- Hybrid: Add soft checkpoints at 20%, 40%, 60% + keep 80% HALT

---

### 6. FTS5 with Auto-Sync Triggers

```sql
CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
```

**Benefit:** BM25 ranking, no manual index maintenance, LIKE fallback.

---

## GRIP vs Cortex Comparison

| Aspect | Cortex | GRIP |
|--------|--------|------|
| **Focus** | Memory retrieval | Session continuity |
| **Storage** | SQLite WASM blob | Git + readable files |
| **Embeddings** | Nomic v1.5 local ONNX | Nomic via API |
| **Search** | Hybrid RRF | grep-based |
| **Auto-save** | 5% increments | 80% gate |
| **Multi-user** | Single-user | N-mind team |
| **Audit trail** | Session IDs | Full git history |
| **Recovery** | 5-backup rotation | Git reflog + instances |

---

## Performance Analysis

### Sub-100ms Search Achieved Via:

1. **WASM SQLite**: In-process, C-level performance
2. **768-dim embeddings**: Half of OpenAI's 1,536 = faster cosine
3. **FTS5 BM25**: Pre-indexed at insert time
4. **Parallel execution**: Vector + keyword run concurrently

### Actual Timing:
- Embedding generation: ~50-200ms
- Vector search: <5ms for 1,000 memories
- Keyword search: <10ms with FTS5
- Total: ~100-300ms including embedding

---

## Attribution Template

For any adopted code:

```markdown
## Attribution

Hybrid search algorithm adapted from [Cortex](https://github.com/hjertefolger/cortex)
by Tomas Hjertefolger. MIT License.

Specific techniques:
- Reciprocal Rank Fusion (RRF) for combining vector + keyword results
- Recency decay with configurable half-life
- Content deduplication via SHA256 truncation
```

---

## Related GRIP Files

- `skills/chat-history-search/SKILL.md` - Current history search
- `lib/instance_serializer.py` - Session persistence
- `skills/context-gate/SKILL.md` - Context threshold handling
- `embeddings.db` - Existing vector storage

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-01-26 | Initial analysis from Gen 200 session |

---

*Analysis conducted by GRIP instance 1ed8da7a, Gen 200*
