# Acknowledgments

GRIP is grateful to the following projects and individuals:

---

## Cortex by Tomas Hjertefolger

**Repository:** [github.com/hjertefolger/cortex](https://github.com/hjertefolger/cortex)
**License:** MIT License
**Copyright:** © 2025 Tomas Hjertefolger

Tomas Hjertefolger's **Cortex** pioneered several breakthrough approaches to persistent local memory for Claude Code.

### What Cortex Is

A sophisticated local-first memory system that provides:
- Hybrid search combining vector similarity, keyword matching, and recency weighting
- Auto-archival at regular context intervals
- SQLite FTS5 full-text search
- Session transcript parsing with semantic chunking
- Privacy-first design keeping all data local

### What GRIP Adapted from Cortex

1. **Hybrid Search (Reciprocal Rank Fusion)**
   - Combines vector similarity (60%) with keyword matching (40%)
   - Uses RRF algorithm (k=60) to merge results
   - Better recall than vector-only search

2. **Auto-Memory Snapshots**
   - Saves semantic snapshots every 10k tokens (~5% of context)
   - Soft checkpoints at 20%, 40%, 60%

3. **Full-Text Search (FTS5)**
   - SQLite FTS5 virtual tables for keyword search
   - Sync triggers between embeddings and FTS index

4. **Recency Decay Weighting**
   - Exponential decay with 7-day half-life
   - Recent memories rank higher

5. **Backup Rotation Strategy**
   - Keeps 5 most recent snapshots per session
   - Automatic pruning

### Differences from Cortex

GRIP is **not a fork** of Cortex. Key differences:

| Aspect | Cortex | GRIP |
|--------|--------|------|
| **Focus** | Local memory | Safety gates + efficiency |
| **Safety** | Not addressed | Deterministic gates |
| **Team Support** | Single-user | Multi-user signatures |
| **Skill System** | N/A | 82 skills |
| **Design Principles** | N/A | SOLID, GRASP, DRY, KISS, YAGNI |

### Recognition

Tomas's work on hybrid search and auto-archival is excellent engineering. GRIP adapted these patterns for its architecture.

- Cortex is listed in GRIP's technical influences
- MIT license terms are respected
- Original implementation stays with Cortex

---

## anthropics/claude-for-legal

**Repository:** [github.com/anthropics/claude-for-legal](https://github.com/anthropics/claude-for-legal)
**License:** Apache License, Version 2.0
**Copyright:** © Anthropic PBC

`skills/legal-ops` absorbs the workflow shapes and legal-domain content from
claude-for-legal (12 practice verticals, cloned 2026-07-02): contract review,
privacy triage, claims substantiation, diligence, IP first-pass, litigation
intake, regulatory watch, and AI-governance triage, each with its reviewer-
note, privilege, and consequential-act gates and tag vocabulary.

### What GRIP Added on Top

- GRASP-native receipts closing every workflow
- IDR-signed decision records and overrides
- A deterministic citation floor (a fabricated quote cannot pass)
- HAL's model-agnostic routing
- The HAPPI/1.3 protocol surface

See LICENSES/Apache-2.0.txt for the full license text and NOTICE for the
copyright statement.

---

## Technical Acknowledgments

### Anthropic
- Claude models
- Claude Code

### Python Ecosystem
- all-MiniLM-L6-v2 (sentence-transformers)
- SQLite with FTS5
- NumPy, PyYAML

---

*Contributions welcome. See CONTRIBUTING.md.*
