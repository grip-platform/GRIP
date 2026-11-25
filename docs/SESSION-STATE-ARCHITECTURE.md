# Session State Architecture

How GRIP stores, retrieves, and merges session task state through instance JSON and KONO memory.

## Problem

Previously, session state lived in a flat `next_up.md` file per project. This caused:

- **Overwrite conflicts**: Branch alpha overwrites what branch main wrote
- **No lineage**: State disconnected from the session tree
- **No semantic search**: Plain markdown invisible to KONO memory queries
- **Single writer**: Last session wins, parallel branches impossible

## Solution

Session state now lives in two complementary locations:

1. **Instance JSON** (`next_up` field) - structured source of truth per session
2. **KONO episodic memory** - semantic index for cross-session retrieval

```text
Instance JSON (per session)
├── conversation         # Session transcript reference
├── lineage              # Parent/child relationships
├── mental_state         # Functional state markers
├── identity             # User identity
├── kono                 # KONO integration metadata
└── next_up              # SESSION STATE (structured)
    ├── completed[]      # Tasks finished this session
    ├── remaining[]      # Tasks still pending
    ├── blockers[]       # Active blockers
    ├── notes            # Free-form (credentials, commands, etc.)
    └── updated_at       # ISO 8601 timestamp
```

## Data Flow

### Write Path (Session End)

```text
Session Work
    │
    ▼
instance-serializer.py
    │
    ├──► Instance JSON file
    │    projects/{project-id}/grip/{instance-sha}.json
    │    Contains structured next_up field
    │
    └──► KONO episodic memory
         store_session_memory(
           content="Remaining: task1; task2 | Blockers: blocker1",
           memory_type="episodic",
           project_path=...
         )
```

The serializer:

1. Extracts task state from the session conversation
2. Stores structured `next_up` dict in the instance JSON
3. Writes remaining tasks and blockers to KONO as episodic memory
4. Both writes happen atomically at serialization time

### Read Path (Resurrection)

Two resurrection modes, both powered by KONO:

```text
grip resume latest              grip fresh gen:N
    │                               │
    ▼                               ▼
Load instance JSON              KONO spatial query
    │                               │
    ├─ Structured next_up           ├─ Episodic recall of
    │  (exact task lists)           │  remaining tasks
    │                               │
    ▼                               ▼
KONO enrichment                 Semantic context only
    │                               (no instance file needed)
    ├─ Cross-session patterns
    ├─ Related memories
    └─ Spatial neighbors
```

- **`grip resume`**: Loads instance JSON for exact task lists, then enriches via KONO
- **`grip fresh`**: Uses KONO alone - episodic recall surfaces remaining work semantically

## Branch Merging (Confluence)

When parallel branches merge, `MergedGRIP.get_next_up()` combines state:

```text
Branch A                    Branch B
next_up:                    next_up:
  completed: [a, b]          completed: [c, d]
  remaining: [x, y]          remaining: [y, z]
  blockers:  [p]              blockers:  [q]
  notes: "A notes"           notes: "B notes"
         │                          │
         └──────────┬───────────────┘
                    ▼
            Merged Instance
            next_up:
              completed: [a, b, c, d]       # union
              remaining: [x, z]             # union - completed, deduped
              blockers:  [p, q]             # union
              notes: "[Branch A] A notes    # labeled concat
                      [Branch B] B notes"
```

Key merge rules:

- `completed` = union of all branches (deduplicated)
- `remaining` = union minus anything in `completed` (deduplicated)
- `blockers` = union of all branches (deduplicated)
- `notes` = concatenated with branch labels for traceability

## KONO Integration

KONO is the unified retrieval layer for all resurrection paths. It is not a parallel system - it is omnipresent.

### What Gets Stored

At serialization time, if remaining tasks or blockers exist:

```python
substrate.store_session_memory(
    content="Remaining: task1; task2 | Blockers: blocker1",
    memory_type="episodic",
    project_path=str(self.project_path),
)
```

This creates a vector-embedded episodic memory in KONO's 4D memory space (semantic, temporal, spatial, emotional dimensions).

### How It Is Retrieved

KONO spatial queries during resurrection surface these memories alongside:

- Previous session summaries
- Procedural knowledge (how-to patterns)
- Emergent insights (cross-session correlations)

This means even a `grip fresh` session (no instance file) can recover task context through semantic similarity.

## Instance JSON Structure

Full instance JSON with the `next_up` field:

```json
{
  "instance_id": "a5e3570c...",
  "generation": 348,
  "timestamp": "2026-02-07T10:00:00.000Z",
  "project_path": "~/project",
  "lineage": {
    "parent": "b2c4d6e8...",
    "root": "139efc67..."
  },
  "conversation": {
    "message_count": 45,
    "summary": "Refactored session state into instance tree..."
  },
  "next_up": {
    "completed": [
      "Add next_up field to instance serializer",
      "Update MergedGRIP for branch merging",
      "Remove file-based state from hooks"
    ],
    "remaining": [
      "Update remaining skills to use instance API",
      "Delete legacy next_up.md files"
    ],
    "blockers": [],
    "notes": "Migration phase 3 complete. Phase 4 in progress.",
    "updated_at": "2026-02-07T10:00:00.000Z"
  },
  "kono": {
    "memories_stored": 3,
    "last_store": "2026-02-07T10:00:00.000Z"
  }
}
```

## Migration

### Backward Compatibility

The instance serializer reads existing `next_up.md` files during the migration period:

```python
candidates = [
    self.project_path / "next_up.md",
    self.project_path / ".claude" / "next_up.md",
]
```

If found, it parses markdown sections (`## Completed`, `## Remaining`, `## Blockers`, `## Notes`) into the structured dict. This ensures no data loss during transition.

### Migration Phases

| Phase | Status | Description |
|-------|--------|-------------|
| 1. Add `next_up` to serializer | Done | Additive, no breakage |
| 2. Update resurrection loader | Done | Parallel read path |
| 3. Remove file-based state | Done | Hooks/lifecycle cleaned |
| 4. Update skills/commands | Done | Instance API throughout |
| 5. Delete flat files | Done | Clean removal |

## Files Involved

| File | Role |
|------|------|
| `lib/instance_serializer.py` | Writes `next_up` to instance JSON + KONO |
| `lib/grip_merged.py` | Merges `next_up` across branches |
| `lib/session-state.sh` | Backward-compatible shell API (delegates to instance) |
| `lib/session-lifecycle.sh` | Session phase detection (instance-based) |
| `lib/kono_substrate.py` | KONO memory storage and retrieval |
| `hooks/session-end.sh` | Triggers serialization at session end |

## Relationship to Other Systems

```text
┌─────────────────────────────────────────────────────────┐
│                    GRIP Session                          │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  Instance JSON                  KONO Memory Substrate    │
│  (structured state)             (semantic index)         │
│  ┌─────────────────┐           ┌─────────────────┐     │
│  │ next_up          │──write──►│ episodic memory  │     │
│  │ lineage          │          │ procedural       │     │
│  │ conversation     │◄─enrich──│ emergent         │     │
│  │ identity         │          │ declarative      │     │
│  └────────┬────────┘           └────────┬────────┘     │
│           │                              │               │
│           ▼                              ▼               │
│  grip resume (exact)          grip fresh (semantic)      │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

- **Instance JSON** = exact structured state (task lists, blockers, notes)
- **KONO** = semantic enrichment (cross-session patterns, similarity search)
- **Both together** = complete resurrection with both precision and context
