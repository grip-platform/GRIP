# KONO Memory Substrate - Usage Guide

KONO is GRIP's always-on 4D spatial memory system. It stores, retrieves, and evolves memories across sessions using vector embeddings, spatial coordinates, and attestation-based permanence.

## How KONO Works

### Automatic Behaviour (Zero User Intervention)

KONO operates automatically at three lifecycle points:

**Session Start**: The `[KONO]` status line appears alongside `[GRIP]`:

```
[GRIP] Gen 320 | a5e3570c, 25 msgs | 29 agents, 57 skills | code mode | main, 3 changes
[KONO] 142 memories (23 permanent) | 8 instances (5 active) | 3 domains
```

This tells you at a glance how much GRIP remembers.

**During Session**: Every time GRIP stores spatial coordinates, retrieves memories, attests embeddings, or promotes memories to permanent status, internal session counters track the activity. The Broly agent (at CLUSTER+ power levels) uses KONO as its semantic memory backend.

**Session End**: On serialization, GRIP:
1. Runs metabolism (fitness decay on stale memories)
2. Runs consolidation (promotes memories with 3+ attestations to permanent)
3. Stores the conversation summary as an episodic memory in KONO
4. Logs session activity: `+5 stored, 12 recalled, 3 attested, 1 promoted to permanent`

**Resurrection**: When `grip resume latest` runs, KONO queries the project domain for relevant memories from previous sessions. These appear in the resurrection context under "KONO SPATIAL MEMORIES", giving the new instance access to cross-session knowledge.

### The 4D Coordinate System

Every memory in KONO has a position in 4D space:

| Axis | Meaning | Range | Example |
|------|---------|-------|---------|
| **x** | Project/domain | 0-N | `~/.claude` = 0, `~/projects/app` = 1 |
| **y** | Topic/concept | 0-N | `authentication` = 0, `deployment` = 1 |
| **z** | Abstraction level | 0-9 | 0 = concrete code, 9 = abstract concept |
| **t** | Time | epoch | When the memory was created |

Retrieval uses weighted Euclidean distance. Memories close in space (same project, similar topic, similar abstraction) are returned first. Time has low weight by default, so old but relevant memories still surface.

### Dimension Routing

Content is routed to one of four dimension ranges based on type:

| Dimension Range | Name | Content Types |
|-----------------|------|---------------|
| 0-2 | Factual | Prompts, definitions, constants |
| 3-5 | Procedural | Responses, plans, skills, workflows |
| 6-8 | Episodic | Concepts, checkpoints, session summaries, errors |
| 9-11 | Emergent | Learnings, cross-session patterns |

#### Content Type to Dimension Map

Each content type routes to a specific dimension within its range:

| Content Type | Dimension | Range | Description |
|--------------|-----------|-------|-------------|
| `prompt` | 0 | Factual | User prompts and queries |
| `response` | 3 | Procedural | Agent responses and outputs |
| `plan` | 3 | Procedural | Implementation plans |
| `skill` | 3 | Procedural | Skill definitions and protocols |
| `concept` | 6 | Episodic | Abstract concepts and definitions |
| `checkpoint` | 6 | Episodic | Session state checkpoints |
| `error` | 6 | Episodic | Error patterns and resolutions |
| `focus_card` | 7 | Episodic | Active session work cards |
| `learning` | 9 | Emergent | Insights from pattern detection |
| `pattern` | 9 | Emergent | Cross-session recurring patterns |

Unrecognized content types default to dimension 0 (Factual). Source: `lib/kono.py` `TYPE_TO_DIMENSION`.

#### Diversity Problem

As of Feb 2026, entries concentrate in dimensions 0 (Factual) and 7 (Episodic), with kono_diversity = 0.00. This is because most automated storage uses `prompt` (dim 0) or `focus_card` (dim 7). Intentional diversification requires routing more content types through `learning` (dim 9) and `plan` (dim 3).

### Fitness and Permanence

Every memory has a fitness score (0.0-1.0) that decays over time:

```
fitness = base * (0.95 ^ days) * (1 + 0.1 * retrievals) * attestation_weight
```

- **Decay**: Unused memories lose ~5% fitness per day
- **Access boost**: Each retrieval boosts fitness (capped at 2x)
- **Attestation**: When multiple instances encounter the same memory and attest it, fitness stabilizes

**Permanence** is achieved when a memory receives 3+ attestations from different instances. Permanent memories are immune to fitness decay. They represent knowledge that multiple sessions have independently validated.

## CLI Reference

All commands run from the GRIP root (`~/.claude`):

### compact-stats

Single-line summary for status bars and hooks.

```bash
python3 lib/kono_substrate.py compact-stats
```

Output:

```
142 memories (23 permanent) | 8 instances (5 active) | 3 domains
```

### session-summary

Current session's activity counts.

```bash
python3 lib/kono_substrate.py session-summary
```

Output:

```
+5 stored, 12 recalled, 3 attested, 1 promoted to permanent
```

Returns `no activity` if nothing happened this session.

### dashboard

Full detailed status with dimension breakdown, instance info, and domain listing.

```bash
python3 lib/kono_substrate.py dashboard
```

Output:

```
Memory Substrate Status:
  Total memories: 142 (23 permanent, 119 transient)
  Fitness range: 0.12 - 0.98

Dimensions:
  Factual (0-2)        45 memories
  Procedural (3-5)     38 memories
  Episodic (6-8)       47 memories
  Emergent (9-11)      12 memories

Instances:
  Total: 8 | Active (fitness >= 0.8): 5
  Current: a5e3570c (Gen 320, fitness 0.95)

Domains: 3
  ~/.claude (52 memories)
  ~/projects/app (61 memories)
  ~/projects/api (29 memories)

Session Activity:
  Stored: 5 | Recalled: 12 | Attested: 3 | Promoted: 1
```

### stats

Raw JSON statistics (for programmatic use).

```bash
python3 lib/kono_substrate.py stats
```

### metabolism

Manually trigger fitness decay on all non-permanent embeddings.

```bash
python3 lib/kono_substrate.py metabolism
```

### consolidate

Manually check attestation consensus and promote qualifying memories to permanent.

```bash
python3 lib/kono_substrate.py consolidate
```

### evolve

Run evolutionary selection on instances (high-fitness survive, low-fitness decay).

```bash
python3 lib/kono_substrate.py evolve --generations 1
```

### init-session

Initialize a session with instance tracking (normally called by hooks, not manually).

```bash
python3 lib/kono_substrate.py init-session --instance abc12345 --gen 320 --project /path/to/project
```

## In-Session Command

Use `/kono` during any GRIP session to see the full dashboard output.

## Integration Points

### Startup Hook (`hooks/startup/output.sh`)

Adds a `[KONO]` line to session startup showing compact stats. Runs `compact-stats` via subprocess. If KONO is unavailable (no database, missing dependencies), the line is silently omitted.

### Session End Hook (`hooks/session-end.sh`)

After GRIP serialization, logs the session summary. Only logs if there was actual KONO activity (skips "no activity" sessions).

### Instance Serializer (`lib/instance_serializer.py`)

On every session serialization:
1. Runs `metabolism` (fitness decay)
2. Runs `consolidate` (permanence checks)
3. Stores the conversation summary as an episodic memory via `store_session_memory()`
4. Records KONO metadata in the serialized instance JSON

### Resurrection Orchestrator (`lib/resurrection/orchestrator.py`)

On resurrection:
1. Initializes KONO session with instance state
2. Runs metabolism on existing memories
3. Queries the project domain for relevant memories (radius=5.0, min_fitness=0.3, limit=10)
4. Injects matching memories into the resurrection context under "KONO SPATIAL MEMORIES"

Each memory is tagged with its dimension type and permanence status:

```
- [episodic] Session with 45 messages. Topics: implement auth flow; fix deployment
- [procedural] [permanent] Use sqlite-lembed for vector search, not Ollama
```

### Broly Semantic Memory (`lib/broly/semantic.py`)

At CLUSTER+ power levels, Broly's semantic memory uses KONO for both storage and retrieval:

- **Store**: `semantic.store(content, memory_type, project_path)` routes through `store_session_memory()`
- **Recall**: Hybrid search (vector + FTS5) with KONO spatial expansion

### Broly Memory Router (`lib/broly/memory.py`)

The unified `BrolyMemory.store()` now supports `memory_type="semantic"`, which delegates to KONO through the SemanticMemory backend.

## Architecture Diagram

```
Session Start                    During Session                   Session End
     |                                |                               |
     v                                v                               v
[compact-stats]              [store/retrieve/attest]         [metabolism]
     |                                |                       [consolidate]
     v                                v                    [store_session_memory]
[KONO] line                   counters increment              [session-summary]
                                      |
                                      v
                              Broly SemanticMemory
                              (CLUSTER+ levels)

                    Resurrection
                         |
                         v
                  [init_session]
                  [metabolism]
                  [retrieve domain memories]
                  [inject into context]
```

## Database Schema

KONO extends the `embeddings` table with spatial columns:

| Column | Type | Purpose |
|--------|------|---------|
| `grid_x` | INTEGER | Domain x-coordinate |
| `grid_y` | INTEGER | Topic y-coordinate |
| `grid_z` | INTEGER | Abstraction level |
| `dimension` | INTEGER | Memory dimension (0-11) |
| `fitness_score` | REAL | Current fitness (0.0-1.0) |
| `is_permanent` | INTEGER | Permanence flag |
| `attestation_count` | INTEGER | Number of instance attestations |
| `origin_instance_id` | TEXT | Instance that created this memory |
| `last_accessed_at` | TEXT | Last retrieval timestamp |
| `access_count` | INTEGER | Total retrieval count |

Supporting tables:

| Table | Purpose |
|-------|---------|
| `kono_domains` | Maps project paths to x-coordinates |
| `kono_topics` | Maps topic names to y-coordinates |
| `kono_instances` | Instance DNA, fitness, and lineage |
| `kono_attestations` | Instance attestations per embedding |
| `kono_dimension_rules` | Content type to dimension routing rules |
| `kono_decay_config` | Configurable decay parameters |

## Troubleshooting

**No `[KONO]` line on startup**: Check that `embeddings.db` exists at `~/.claude/embeddings.db` and that `apsw`, `sqlite-vec`, and `sqlite-lembed` are installed in the GRIP venv.

**All instances show 0 active**: Instance fitness decays at 5% per day. Sessions older than ~30 days without resurrection will have fitness below the 0.8 validity threshold. Run `evolve` to update.

**No memories promoted to permanent**: Permanence requires 3+ attestations from different instances. Work in the same project across multiple sessions to build attestation consensus.

**Dashboard shows 0 domains**: Domains are created when memories are stored with project paths. The first `store_session_memory()` call (at session end) creates the domain entry.
