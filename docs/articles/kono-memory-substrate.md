# KONO Memory Substrate: Cross-Session Memory for AI Agents

Every Claude Code session starts with amnesia. No memory of what was decided last Tuesday, which workaround you found for the deployment issue, or the three approaches you already ruled out for the auth refactor. The model is stateless by design — and that statelessness has a compounding cost the longer a project runs.

We built KONO to solve that problem. It is a semantic memory substrate that persists knowledge across Claude Code sessions, structured around a 4D coordinate space, fitness-based decay, and attestation-driven permanence. After roughly 340 sessions of GRIP development, the database holds 1,813 embeddings across 9 domains — 200 of them permanent, meaning multiple independent sessions have independently validated that knowledge.

This article explains how it works.

---

## The Problem: Logs Are Not Memory

The naive solution to agent amnesia is logging. Serialize the session to a file, inject it next time. This works until it doesn't: after enough sessions, the log is larger than the context window, and you're back to forgetting — just more slowly.

A log is also flat. Every entry carries equal weight regardless of whether it was confirmed across ten sessions or hallucinated once and never revisited. There is no mechanism for knowledge to stabilize or for noise to decay.

What we needed was not a longer log. We needed memory with structure: relevance, fitness, and the ability to distinguish what actually held up from what sounded plausible in the moment.

---

## What KONO Is

KONO is a SQLite database with vector embeddings — specifically, sqlite-vec for similarity search and sqlite-lembed for in-process embedding generation. Every piece of knowledge GRIP accumulates gets embedded as a 1,536-dimension vector and stored alongside a 4D spatial coordinate:

| Axis | Meaning | Example |
|------|---------|---------|
| x | Domain (project path) | `~/.claude` = 0, `~/projects/app` = 1 |
| y | Topic/concept | `authentication`, `deployment` |
| z | Abstraction level (0-9) | 0 = concrete code, 9 = abstract principle |
| t | Time (epoch) | When the memory was created |

Retrieval is weighted Euclidean distance across these four dimensions. A query from the GRIP project about authentication surfaces memories that are close in x (same project), y (same topic), and z (similar abstraction level). Time has low weight, so a useful memory from six weeks ago still surfaces over a recent but irrelevant one.

The 1,813 embeddings in the current database are distributed across four dimension ranges:

- **Factual (0-2)**: 983 memories — prompts, definitions, constants
- **Procedural (3-5)**: 2 memories — plans, skills, workflows
- **Episodic (6-8)**: 828 memories — session summaries, checkpoints, errors
- **Emergent (9-11)**: architectural patterns that have generalized across sessions

Content routes to its dimension range automatically based on type. Session summaries go to episodic. Learned rules go to emergent. This keeps search efficient — a question about how to approach a problem queries procedural and emergent space, not the full corpus.

---

## How Fitness Works

Every memory has a fitness score between 0.0 and 1.0 that evolves over time:

```
fitness = base * (0.95 ^ days) * (1 + 0.1 * retrievals) * attestation_weight
```

Unused memories lose approximately 5% fitness per day. Each retrieval boosts fitness (capped at 2x). This means knowledge that keeps being useful stays healthy; knowledge that was stored once and never retrieved again eventually drops below the retrieval threshold and stops surfacing.

The current database has a fitness range of 0.00 to 1.00 — some memories are at full health, others are at the floor, kept only because they are permanent.

**Permanence** is the escape hatch from fitness decay. When a memory accumulates 3 or more attestations from distinct GRIP instances, it becomes permanent. Permanent memories do not decay. They represent knowledge that multiple independent sessions have encountered, used, and confirmed — the closest thing KONO has to ground truth.

200 of the 1,813 memories in the current database are permanent. These include things like "use sqlite-lembed for vector search, not Ollama" (learned after a painful incorrect detour) and structural decisions about the GRIP hook architecture that have held stable across dozens of sessions.

---

## Three Memory Types

KONO stores three categories of knowledge, each with different behavior:

**Episodic** memories are session summaries. At the end of every GRIP session, the conversation is serialized into an episodic memory with a project domain coordinate. These capture what was worked on, what was decided, and what remains. They are the primary source for cross-session continuity.

**Long-term** (LTM) memories come from explicit GRIP learning cycles — `/learn` commands, RSI loop outputs, and the Broly agent's CLUSTER+ reasoning. More abstract than episodic, stored at z = 5-8.

**Permanent** memories have survived attestation consensus. They no longer decay. They are the accumulated institutional knowledge of the GRIP system: architectural decisions, confirmed workarounds, validated patterns, and disproven hypotheses.

---

## The Session Lifecycle

KONO operates automatically at three points in every GRIP session.

At **session start**, the startup hook queries KONO for the compact stats line:

```
[KONO] 1813 memories (200 permanent) | 2461 instances | 9 domains
```

At **session end**, three things happen: fitness decay runs on all non-permanent memories (metabolism), attestation counts are checked and qualifying memories are promoted to permanent (consolidation), and the session conversation summary is embedded and stored as a new episodic memory.

At **resurrection** — when `grip resume latest` is called — the orchestrator queries the current project's domain for memories with fitness above 0.3, within spatial radius 5.0, up to 10 results. These are injected into the resurrection context:

```
- [episodic] Session with 45 messages. Topics: implement auth flow; fix deployment
- [procedural] [permanent] Use sqlite-lembed for vector search, not Ollama
```

The new session instance begins with the relevant history already present, without requiring the full conversation log to be injected.

---

## What KONO Enables

**Cross-session continuity without context window pressure.** Rather than injecting 50,000 tokens of session history, KONO injects the 10 most relevant memories — typically under 1,000 tokens. The agent starts informed rather than starting blank.

**GRIP resurrection.** The `grip resume latest` command works because KONO surfaces what the previous session was doing, what it decided, and what it left unfinished — populated from KONO's episodic layer.

**Fitness-based knowledge evolution.** Knowledge that proves useful across sessions becomes more retrievable. Knowledge that was wrong or irrelevant decays. Over 340 generations, this means the GRIP system has implicitly learned which of its own patterns hold up.

**Broly semantic memory.** At CLUSTER+ power levels, the Broly meta-agent uses KONO as its semantic memory backend. When Broly spawns parallel subagents, each subagent's learnings are stored to KONO and available to subsequent Broly invocations in the same project domain.

**Attestation as epistemic weight.** When multiple independent sessions encounter and retrieve the same knowledge, it gains attestation weight and approaches permanence. Not rigorous peer review, but substantially better than single-session assertions.

---

## Honest Limitations

KONO is not RAG. Memories are injected into context as text — the model reads them the same way it reads anything else. Whether it uses them depends on embedding quality and query relevance. The model weights do not change; KONO is a context management system with a semantic index, not a learning system in the machine learning sense.

KONO requires GRIP infrastructure: sqlite-lembed, the hook system, and the instance serializer. It does not work out of the box on a fresh machine.

The 2,461 instances in the database have 0 active (fitness >= 0.8). Instance fitness decays at 5% per day, and sessions older than roughly 30 days without resurrection fall below the validity threshold. This is expected — the system treats old, unresurrected sessions as inactive — but it means the instance graph requires regular use to stay informative.

---

## Architecture

SQLite with sqlite-vec. Embeddings at 1,536 dimensions, generated in-process via sqlite-lembed. Spatial indexing via four columns (grid_x, grid_y, grid_z, dimension). Fitness tracked per embedding with exponential decay. Permanence via attestation table with multi-instance consensus. Session counters persisted to JSON with file locking for multi-process safety. Integrated with GRIP hooks at session start, session end, and resurrection.

The production instance at `~/.claude/embeddings.db` is 340 generations old, spans 9 projects, and carries 200 memories that have survived multi-session attestation. That is the knowledge base GRIP opens with every time.

---

*This article covers KONO as implemented in GRIP v6.0.0, Gen 341. Stats reflect the live production instance as of February 2026.*
