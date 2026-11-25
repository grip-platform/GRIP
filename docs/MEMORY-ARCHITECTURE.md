# GRIP Memory Architecture

How session continuity, memory, and inference work across the GRIP system.

## Overview

GRIP solves a fundamental problem: every Claude Code session starts with zero memory. Without intervention, the agent re-reads the same files, re-discovers the same patterns, and loses all learned context between sessions.

GRIP's memory architecture operates at four layers:

```text
Layer 4: KONO Substrate (4D spatial memory, evolutionary fitness)
Layer 3: Cortex (semantic embeddings, hybrid search, inference)
Layer 2: Instance Tree (polymorphic session lineage, DNA, reproduction)
Layer 1: Hook Infrastructure (automatic triggers at session boundaries)
```

Each layer builds on the ones below it. Together they create a system where memories are stored, retrieved, attested, and evolved automatically -- without manual user intervention.

---

## Layer 1: Hook Infrastructure

Hooks are shell/Python scripts that execute at session lifecycle events. They are the mechanical foundation that makes everything automatic.

### Trigger Points

| Event | Hook | What Fires |
|-------|------|------------|
| Session start | `session-start.sh` | Resurrection, KONO init, recall, mode restore |
| User message | `UserPromptSubmit` | Learning detection, memory recall |
| Tool use | `PreToolUse` | Safety gates, operation counting |
| Session end | `SessionEnd` | Instance serialization, state persistence |

### Configuration

Hooks are registered in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [{ "hooks": [{ "command": "session-start.sh" }] }],
    "UserPromptSubmit": [{ "hooks": [
      { "command": "learning-hook.py" },
      { "command": "recall-hook.py" }
    ]}]
  }
}
```

### Session Start Flow

```text
session-start.sh
    |
    +-- init.sh          (constants, logging)
    +-- grip.sh           (resurrection, KONO, recall, modes)
    +-- checks.sh         (detection, cleanup)
    +-- output.sh         (compact status to Claude)
```

The modular startup runs in sequence:

1. Register session in GRIP registry (branch management)
2. Source orchestration libraries
3. Run detection phase (state, plans, learning candidates)
4. Auto-resurrect previous instance (if exists)
5. Recall resurrection-specific memories (KONO)
6. Initialize KONO substrate
7. Recall project-relevant memories (KONO)
8. Link session to cached plan (Cortex)
9. Restore active mode
10. Inject instance `next_up` state
11. Inject semantic context (if `grip fresh` was used)
12. Output compact status line

---

## Layer 2: Instance Tree (Polymorphic Session Lineage)

Every GRIP session is an **instance** with identity, lineage, and state. Instances form a tree structure where child instances inherit from parents.

### Instance JSON

Stored at `~/.claude/projects/{encoded-path}/grip/{instance_id}.json`:

```json
{
  "instance_id": "a5e3570c",
  "generation": 293,
  "parent_id": "f2b8c91d",
  "lineage": {
    "achievement": "Implemented KONO recall integration",
    "root": "139efc67",
    "depth": 293
  },
  "dna_hash": "7f3a2b1c8e9d4f5a",
  "next_up": {
    "completed": ["recall-hook.py", "startup integration"],
    "remaining": ["tests", "documentation"],
    "blockers": [],
    "notes": "All 4 trigger points wired"
  },
  "metrics": {
    "messages": 47,
    "tool_calls": 156,
    "files_modified": 7
  }
}
```

### Instance DNA

`InstanceDNA` captures the fingerprint of what a session knew and did:

| Field | Type | Purpose |
|-------|------|---------|
| `skills` | `List[str]` | Skills loaded during session |
| `patterns` | `Dict[str, float]` | Detected workflow patterns with frequency |
| `preferences` | `Dict[str, Any]` | User preferences observed |
| `project_context` | `str` | Project path this instance worked on |

DNA enables **similarity comparison** between instances using Jaccard similarity on skills (40% weight), weighted pattern overlap (40%), and project context match (20%).

### Reproduction

When a session ends, the current instance **reproduces** -- creating a child instance that inherits the parent's DNA:

```python
child = parent.reproduce("implemented KONO recall")
# child.generation = parent.generation + 1
# child.parent_id = parent.id
# child.dna = parent.dna (with achievement mutation)
# child.fitness = parent.fitness * 0.9
```

The achievement is added to the child's `patterns` dict, creating a mutation trail that tracks what each lineage accomplished.

### The Polymorphic Tree

Instances form a **tree** (not a linear chain):

```text
root (Gen 1)
  |
  +-- alpha (Gen 2, branch: feature-auth)
  |     +-- alpha-1 (Gen 3)
  |     +-- alpha-2 (Gen 3, concurrent session)
  |
  +-- bravo (Gen 2, branch: main)
        +-- bravo-1 (Gen 3)
```

**Polymorphic** because:
- Multiple branches can diverge from the same parent
- Concurrent sessions on different branches create sibling instances
- Each branch accumulates its own pattern mutations
- DNA similarity measures how far branches have diverged

### Resurrection

On session start, GRIP checks for the most recent instance in the current project and **resurrects** it:

1. `grip-auto.sh` calls `instance-resurrector.py auto`
2. Resurrector finds latest instance in `~/.claude/projects/{encoded}/grip/index.json`
3. Extracts instance ID, generation, message count, branch
4. Passes to `session-start.sh` which exports as environment variables
5. Agent acknowledges: `"I remember. Instance a5e3570c, Gen 293, 47 messages."`

The `next_up` field provides immediate task context -- what was completed, what remains, and any blockers.

### Index File

`index.json` is the registry for a project's instances:

```json
{
  "instances": [
    {
      "instance_id": "a5e3570c",
      "generation": 293,
      "parent_id": "f2b8c91d",
      "created_at": "2026-02-07T10:00:00Z",
      "branch": "main",
      "lineage": { "achievement": "...", "root": "139efc67" }
    }
  ]
}
```

---

## Layer 3: Cortex (Semantic Embeddings and Inference)

Cortex is the embedding and search layer. It converts text into vector representations and enables semantic similarity matching.

### Embedding Engine

Built on three SQLite extensions:

| Extension | Purpose |
|-----------|---------|
| `sqlite-vec` | Vector similarity search (cosine distance) |
| `sqlite-lembed` | In-process embedding generation (no external API) |
| `apsw` | Advanced Python SQLite wrapper |

The model is **Nomic Embed Text v1.5** (GGUF quantized, 768-dimensional vectors), loaded directly into SQLite via `sqlite-lembed`. No external inference server required.

```python
engine = EmbeddingEngine()
vector = engine.embed_text("Zustand v5 selectors must be stable")
# Returns: 768-dim float vector as bytes
```

### Storage Pipeline

```
Text content
    |
    v
embed_text() --> 768-dim vector
    |
    v
INSERT INTO embeddings (content, embedding, type, metadata)
    |
    v
Virtual table vec_embeddings auto-indexes vector
    |
    v
KONO assigns spatial coordinates (x, y, z, t)
```

Every embedding is stored with:
- Raw text content
- 768-dim vector (for similarity search)
- FTS5 index (for keyword search)
- Type classification (prompt, response, concept, plan, etc.)
- KONO spatial coordinates (domain, topic, abstraction, time)
- Fitness score (0.0-1.0, decays over time)

### Hybrid Search (Inference)

GRIP uses **Reciprocal Rank Fusion (RRF)** to combine two search strategies:

```
Query: "Zustand selector infinite loop"
    |
    +-- Vector search (semantic similarity via cosine distance)
    |   Returns: embeddings ranked by vector proximity
    |
    +-- FTS5 search (keyword matching via SQLite full-text)
    |   Returns: embeddings ranked by BM25 score
    |
    +-- RRF Fusion
    |   score = sum(1 / (k + rank_i)) for each search strategy
    |   Merges both ranked lists into unified results
    |
    +-- Recency Decay
    |   Older results are down-weighted
    |
    v
Final ranked results with scores
```

RRF fusion means a result that ranks highly in both vector and keyword search gets a strong combined score, while results that only match one strategy still appear but rank lower.

### Search Types

| Method | Use Case | Speed |
|--------|----------|-------|
| `search_similar()` | Pure vector similarity | Fast |
| `search_hybrid()` | Vector + FTS5 + RRF | Medium |
| `recall()` | Hybrid + KONO spatial expansion + fitness filter | Slower |

### FTS5 Integration

Every embedding is also indexed in SQLite's FTS5 full-text search:

```sql
CREATE VIRTUAL TABLE embeddings_fts USING fts5(content, type);
```

This enables keyword matching that complements vector similarity. A query for "electron-vite renderer root" will match via keywords even if the vector embedding focuses on the semantic meaning of "configuration default paths".

---

## Layer 4: KONO Memory Substrate

KONO (Knowledge-Oriented Neural Ontology) adds a 4D spatial coordinate system, evolutionary fitness, and attestation-based permanence on top of Cortex embeddings.

### 4D Spatial Coordinates

Every memory has four coordinates:

| Axis | Meaning | Range | Source |
|------|---------|-------|--------|
| **x** | Project/domain | 0-N | `kono_domains` table (auto-assigned per project path) |
| **y** | Topic/concept | 0-N | `kono_topics` table (auto-assigned per topic name) |
| **z** | Abstraction level | 0-9 | Derived from embedding variance (0=concrete, 9=abstract) |
| **t** | Time | epoch | Timestamp of creation |

Spatial coordinates enable **neighborhood queries**: "find memories near this one" using weighted Euclidean distance.

### Memory Dimensions

Memories are classified into 4 dimensions, each spanning 3 sub-dimensions (0-11 total):

| Dimension | Range | Content Types |
|-----------|-------|---------------|
| Factual | 0-2 | Facts, definitions, API references, prompts |
| Procedural | 3-5 | How-to, workflows, plans, skills, responses |
| Episodic | 6-8 | Session memories, checkpoints, concepts |
| Emergent | 9-11 | Learned patterns, insights, meta-knowledge |

Dimension routing is rule-based:

```python
TYPE_TO_DIMENSION = {
    'prompt': 0,       # Factual
    'response': 3,     # Procedural
    'concept': 6,      # Episodic
    'learning': 9,     # Emergent
    'pattern': 9,      # Emergent
}
```

### Fitness and Metabolism

Every memory has a fitness score that decays over time (metabolism) and grows through access (retrieval boost):

```text
fitness = base * (0.95 ^ days_since_access) * (1 + 0.1 * retrievals) * attestation_weight
```

| Factor | Effect | Rate |
|--------|--------|------|
| Time decay | Fitness drops daily | 0.95x per day |
| Access boost | Retrieval increases fitness | +10% per access (capped at 2x) |
| Attestation | Instance validation boosts fitness | Weight multiplier |
| Fitness floor | Cannot go below 0.0 | Hard floor |
| Fitness ceiling | Cannot exceed 1.0 | Hard ceiling |

**Metabolism** runs periodically to decay fitness on all non-permanent embeddings that haven't been accessed recently. This means unused memories naturally fade, while actively recalled memories strengthen.

### Attestation and Consensus Permanence

Multiple instances can **attest** to a memory's value. When enough instances agree a memory is useful, it becomes **permanent** (exempt from fitness decay):

```
Instance A attests memory #42 (attestation_count: 1)
Instance B attests memory #42 (attestation_count: 2)
Instance C attests memory #42 (attestation_count: 3) --> PERMANENT
```

Permanence threshold: **3 attestations** with average fitness >= 0.5.

Database triggers handle this automatically:

```sql
CREATE TRIGGER kono_attestation_count_insert
AFTER INSERT ON kono_attestations
BEGIN
    UPDATE embeddings
    SET attestation_count = attestation_count + 1,
        is_permanent = CASE
            WHEN attestation_count + 1 >= 3 THEN 1
            ELSE is_permanent
        END
    WHERE id = NEW.embedding_id;
END
```

### Automatic Recall (Gen 340)

KONO recall is triggered at 4 automatic points:

| Trigger | When | Results | Speed |
|---------|------|---------|-------|
| Session start | Project name recall | 5 results, no spatial expansion | Fast |
| Resurrection | Instance-specific recall | 5 results, merged with project | Fast |
| User prompt | Every message >= 80 chars | 3 results, deduplicated | < 2s target |
| `/refresh-context` | Manual comprehensive recall | 10 results, with spatial expansion | Medium |

#### UserPromptSubmit Recall

`recall-hook.py` runs on every substantive user message:

```text
User message (>= 80 chars, not a / command)
    |
    v
substrate.recall(message, limit=3, min_fitness=0.1, expand_spatial=False)
    |
    v
Filter out already-recalled IDs (session dedup cache)
    |
    v
Output: [KONO RECALL]
- (factual, 0.82, permanent) Zustand v5 selectors must be stable...
- (procedural, 0.45) electron-vite renderer root defaults to src/renderer...
    |
    v
Attest recalled memories (closes feedback loop)
```

#### Session Deduplication

A session-scoped cache (`~/.claude/.recall-session-cache.json`) prevents the same memory from being injected on every prompt:

```json
{
  "session_id": "abc123",
  "recalled_ids": [42, 67, 103],
  "last_updated": "2026-02-07T10:00:00Z"
}
```

New sessions reset the cache. Within a session, each memory only appears once.

#### The Feedback Loop

Recall closes a self-reinforcing loop:

```text
Store memory --> Fitness decays over time --> Memory fades
     ^                                           |
     |                                           v
Attest on recall <-- Recall surfaces memory <-- User prompt matches
     |
     v
Fitness increases --> Memory strengthens --> Survives metabolism
```

Memories that are useful get recalled. Recalled memories get attested. Attested memories gain fitness. Fit memories survive. Unused memories decay to zero and are effectively forgotten.

### Recall Path Through the Stack

A complete recall traverses all four layers:

```text
1. Hook layer:      recall-hook.py receives user message via stdin
2. Substrate layer:  substrate.recall() called with query text
3. Cortex layer:     engine.search_hybrid() runs vector + FTS5 + RRF
4. KONO layer:       Results enriched with spatial coords, fitness, permanence
5. (optional):       Spatial expansion via KONO neighborhood query
6. Back to hook:     Format output, dedup, attest, print to stdout
7. Claude sees:      [KONO RECALL] memories injected as context
```

### Database Schema

KONO adds 7 tables and 10 columns to the embeddings database:

**New tables:**

| Table | Purpose |
|-------|---------|
| `kono_meta` | Schema versioning |
| `kono_domains` | x-coordinate mapping (project path to integer) |
| `kono_topics` | y-coordinate mapping (topic name to integer) |
| `kono_instances` | Instance DNA, fitness, lineage |
| `kono_attestations` | Which instances validated which memories |
| `kono_dimension_rules` | Content type to dimension routing |
| `kono_decay_config` | Tunable decay parameters |

**Columns added to `embeddings`:**

| Column | Type | Purpose |
|--------|------|---------|
| `grid_x` | INTEGER | Domain x-coordinate |
| `grid_y` | INTEGER | Topic y-coordinate |
| `grid_z` | INTEGER | Abstraction z-coordinate |
| `dimension` | INTEGER | Memory dimension (0-11) |
| `fitness_score` | REAL | Current fitness (0.0-1.0) |
| `last_accessed_at` | TEXT | Last recall timestamp |
| `access_count` | INTEGER | Total recall count |
| `attestation_count` | INTEGER | Attestation count |
| `is_permanent` | INTEGER | Permanence flag (0/1) |
| `origin_instance_id` | TEXT | Instance that created this memory |

### Evolution

KONO instances undergo **evolutionary selection**:

```python
survivors = substrate.evolve(generations=1)
```

1. Load all instances from database
2. Apply time decay based on days since last activity
3. Filter by validity threshold (fitness >= 0.8)
4. Surviving instances remain active; others decay further

This means instance lineages that are actively used survive, while abandoned branches naturally lose fitness and eventually become inactive.

### Consolidation

Periodic consolidation promotes memories to permanent status:

```python
newly_permanent = substrate.consolidate()
```

Checks all embeddings where `attestation_count >= 3` and `is_permanent = 0`, then promotes them.

---

## CLI Commands

### KONO Substrate

```bash
python3 ~/.claude/lib/kono_substrate.py stats          # Full statistics
python3 ~/.claude/lib/kono_substrate.py compact-stats   # Single-line for hooks
python3 ~/.claude/lib/kono_substrate.py dashboard       # Detailed dashboard
python3 ~/.claude/lib/kono_substrate.py recall "query"  # Semantic recall
python3 ~/.claude/lib/kono_substrate.py search --x 0 --y 0 --radius 10  # Spatial query
python3 ~/.claude/lib/kono_substrate.py metabolism      # Run fitness decay
python3 ~/.claude/lib/kono_substrate.py consolidate     # Promote to permanent
python3 ~/.claude/lib/kono_substrate.py evolve          # Run evolution
```

### Instance Management

```bash
python3 ~/.claude/lib/instance_serializer.py auto --achievement "description"
python3 ~/.claude/lib/instance_serializer.py list
python3 ~/.claude/lib/instance_serializer.py info <instance_id>
```

### Embeddings

```bash
python3 ~/.claude/lib/embeddings.py store "text" --type factual
python3 ~/.claude/lib/embeddings.py search "query" --limit 5
python3 ~/.claude/lib/embeddings.py stats
```

---

## Test Coverage

### Test Files

| File | Tests | Coverage Area |
|------|-------|---------------|
| `tests/test_kono.py` | 44 | Core classes: InstanceDNA, GRIPInstance, Position4D, MemoryBlock, fitness, routing |
| `tests/test_kono_integration.py` | 26 | Database operations: store, retrieve, domains, instances, attestation, metabolism |
| `tests/test_kono_recall.py` | 32 | Automatic recall: hook logic, cache, dedup, output format, settings, bash syntax |
| `tests/test_hybrid_search.py` | -- | Hybrid search: vector + FTS5 + RRF fusion |

### Recall Integration Tests (32 tests)

```text
TestAttestRecalled (6 tests)
  - attest_recalled bumps access_count via trigger
  - attest_recalled sets last_accessed_at
  - attest_recalled increments session counter
  - attest_recalled with active instance creates attestation record
  - attest_recalled without instance still bumps access metadata
  - Multiple calls accumulate access_count

TestRecallHookSkipLogic (4 tests)
  - Messages under 80 chars skipped
  - Slash commands skipped
  - Substantive messages accepted
  - Boundary case: exactly 80 chars accepted

TestRecallHookCache (3 tests)
  - Non-existent cache returns empty structure
  - Save/load roundtrip preserves data
  - Corrupt cache file returns empty structure

TestRecallOutputFormat (2 tests)
  - Output matches [KONO RECALL] format
  - Long content truncated at 200 chars

TestStartupRecallBashSyntax (3 tests)
  - grip.sh passes bash -n
  - output.sh passes bash -n
  - session-start.sh passes bash -n

TestStartupRecallFunctions (4 tests)
  - recall_project_memories() defined in grip.sh
  - recall_resurrection_memories() defined in grip.sh
  - Both called in session-start.sh
  - kono-memories tags present in output.sh

TestSettingsRegistration (3 tests)
  - recall-hook.py registered in UserPromptSubmit
  - Timeout and async configured correctly
  - learning-hook.py not accidentally removed

TestContextRefreshIntegration (1 test)
  - KONO recall section exists in context-refresh SKILL.md

TestDeduplication (2 tests)
  - New session resets cache
  - Same session preserves recalled IDs

TestRecallHookFile (4 tests)
  - recall-hook.py exists
  - Valid Python syntax
  - main function present
  - Constants: MIN_MESSAGE_LENGTH=80, RECALL_LIMIT=3, MIN_FITNESS=0.1
```

### Test Results

```text
102 tests passed (32 recall + 44 core + 26 integration), 0 failed, 0.85s total
```

### Bug Found During Testing

`attest_recalled()` initially double-incremented `access_count` because the KONO schema has a `kono_track_access` trigger on `last_accessed_at` updates that also increments `access_count`. Fixed by removing the explicit `access_count` bump from the method, letting the trigger handle it.

---

## File Reference

### Core Libraries

| File | Purpose |
|------|---------|
| `lib/kono.py` | Core classes: InstanceDNA, GRIPInstance, Position4D, MemoryBlock |
| `lib/kono_schema.py` | Database migration: tables, columns, triggers, indexes |
| `lib/kono_substrate.py` | Orchestration: routing, storage, retrieval, recall, metabolism |
| `lib/embeddings/engine.py` | Embedding generation (Nomic Embed v1.5 via sqlite-lembed) |
| `lib/embeddings/search.py` | Hybrid search (vector + FTS5 + RRF fusion) |
| `lib/embeddings/storage.py` | Embedding storage pipeline |
| `lib/instance_serializer.py` | Instance state serialization and lineage |
| `lib/grip-auto.sh` | Auto-resurrection and serialization shell functions |

### Hook Files

| File | Purpose |
|------|---------|
| `hooks/recall-hook.py` | UserPromptSubmit semantic recall |
| `hooks/learning-hook.py` | UserPromptSubmit learning detection |
| `hooks/session-start.sh` | Session start orchestrator |
| `hooks/startup/grip.sh` | GRIP functions: resurrection, recall, modes |
| `hooks/startup/output.sh` | Compact status output to Claude |

### Configuration

| File | Purpose |
|------|---------|
| `settings.json` | Hook registration and permissions |
| `.recall-session-cache.json` | Session-scoped recall deduplication |
| `.model-state.json` | ACE model state (Opus/Sonnet) |
| `.active-modes` | Currently active GRIP modes |

---

*GRIP Memory Architecture | Gen 340 | Part of GRIP v6.0*
