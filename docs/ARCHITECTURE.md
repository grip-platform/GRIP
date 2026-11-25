# GRIP Architecture

System-level architecture overview of GRIP (Guarded Response Interception Protocol).   
Auto-generated from repository topology analysis.

## System Overview

```mermaid
graph TB
    subgraph User["User Entry Points"]
        CC["Claude Code CLI"]
        GG["gg++ / gogrip()"]
        GRIP_CLI["grip CLI (bin/grip)"]
    end

    subgraph Core["Core Infrastructure"]
        CLAUDE_MD["CLAUDE.md<br/>(Session Instructions)"]
        RULES["rules/<br/>(5 rule files)"]
        CONFIG["config/<br/>(grip.json, settings)"]
        IDENTITY["facts/<br/>(identity.md, team.md)"]
    end

    subgraph Engine["Automation Engine"]
        SKILLS["skills/<br/>(270 skills)"]
        AGENTS["agents/<br/>(41 agents)"]
        MODES["modes/<br/>(30 presets)"]
        HOOKS["hooks/<br/>(39 quality gates)"]
    end

    subgraph Libraries["Core Libraries (lib/)"]
        PL["plain_language.py"]
        HE["hypothesis_engine.py"]
        IS["instance_serializer.py"]
        ML["mode-loader.sh"]
        CE["converge_metrics.py"]
        EMBED["embeddings/"]
    end

    subgraph MCP["MCP Servers"]
        CHANNEL["grip-channel<br/>(Bridge)"]
        APPS["grip-apps<br/>(Emergent UI)"]
        GRIP_MCP["grip-mcp-desktop<br/>(GRIP Tools)"]
        CODEIDX["code-index<br/>(AST Search)"]
    end

    subgraph Data["Data + State"]
        INSTANCES["instances/<br/>(Session State)"]
        LEARNING["learning/<br/>(Insights, Hypotheses)"]
        GENOME["data/genomes/<br/>(Feature Scorecard)"]
        KONO["memory/<br/>(Knowledge Base)"]
        EMBDB["embeddings.db<br/>(Vector DB)"]
    end

    GG --> GRIP_CLI
    GRIP_CLI --> CC
    CC --> CLAUDE_MD
    CLAUDE_MD --> RULES
    CLAUDE_MD --> CONFIG
    CC --> HOOKS
    HOOKS --> SKILLS
    CC --> AGENTS
    AGENTS --> SKILLS
    CC --> MCP
    SKILLS --> Libraries
    Libraries --> Data
    CHANNEL --> CC
```

## Directory Layout

```mermaid
graph LR
    ROOT["~/.claude/"]

    ROOT --> CORE_DIRS
    ROOT --> AUTO_DIRS
    ROOT --> DATA_DIRS
    ROOT --> INFRA_DIRS

    subgraph CORE_DIRS["Core (checked in)"]
        direction TB
        C1[".claude/CLAUDE.md"]
        C2["rules/ — operating rules"]
        C3["skills/ — 270 skills"]
        C4["agents/ — 41 agents"]
        C5["modes/ — 30 presets"]
        C6["hooks/ — 39 quality gates"]
        C7["lib/ — 389 Python/Bash libs"]
        C8["scripts/ — install.sh, utilities"]
        C9["bin/ — grip, gg++, grip-ut"]
        C10["docs/ — CONTRIBUTING, ARCHITECTURE"]
    end

    subgraph AUTO_DIRS["Automation (checked in)"]
        direction TB
        A1["commands/ — 84 command docs"]
        A2["config/ — grip.json, cube.json"]
        A3["mcp-servers/ — 5 MCP bridges"]
        A4["templates/ — file templates"]
        A5["tests/ — test suites"]
    end

    subgraph DATA_DIRS["Data (gitignored/encrypted)"]
        direction TB
        D1["instances/ — session state"]
        D2["learning/ — insights, hypotheses"]
        D3["data/ — genomes, embeddings"]
        D4["memory/ — KONO knowledge base"]
        D5["facts/ — identity, team, CV"]
        D6["organizations/ — org configs"]
    end

    subgraph INFRA_DIRS["Runtime (gitignored)"]
        direction TB
        I1["venv/ — Python environment"]
        I2["node_modules/ — Node deps"]
        I3["cache/ — file read cache"]
        I4["state/ — approvals, audit"]
        I5["sessions/ — active sessions"]
        I6["plans/ — execution plans"]
    end
```

## Session Lifecycle

```mermaid
sequenceDiagram
    participant U as User
    participant GG as gg++
    participant G as grip CLI
    participant CC as Claude Code
    participant H as Hooks (39)
    participant S as Skills (212)

    U->>GG: gg++ [tokens]
    GG->>G: grip fresh latest 12000
    G->>G: Resolve session (latest/gen:N/branch:X)
    G->>G: Compress context to token budget
    G->>CC: claude --resume SESSION_ID
    CC->>CC: Load CLAUDE.md + rules/
    CC->>H: PreToolUse gates fire
    H-->>CC: ALLOW / DENY / WARN

    U->>CC: /activate-grip
    CC->>S: Invoke activation wizard
    S->>CC: Create identity.md, settings.json

    U->>CC: /mode code
    CC->>S: Load mode definition
    S->>CC: Activate 'code' preset (skills + rules)

    loop Every Tool Call
        CC->>H: PreToolUse hook
        H-->>CC: Quality gate verdict
        CC->>CC: Execute tool
        CC->>H: PostToolUse hook
        H-->>CC: Advisory signal
    end

    U->>CC: /save
    CC->>CC: Serialize instance state
    CC->>CC: Update KONO memory
    CC->>CC: Persist session context
```

## Bridge Architecture (grip-channel)

```mermaid
graph TB
    subgraph Platforms["External Platforms"]
        WA["WhatsApp<br/>(Baileys)"]
        SL["Slack<br/>(MCP + Direct API)"]
        DC["Discord<br/>(discord.js Bot)"]
        WEB["Web<br/>(your-gateway.example.com)"]
    end

    subgraph Channel["grip-channel MCP Server"]
        CM["Commander Pool<br/>(3 persistent sessions)"]
        CG["Conversation Graph<br/>(Semantic tracking)"]
        GC["Guild Crawler<br/>(Discord knowledge)"]
        SSE["SSE Bridge<br/>(Real-time events)"]
    end

    subgraph CC["Claude Code Sessions"]
        MAIN["Main Session"]
        PAIR["Pair-Mode Partner"]
        RSI["RSI Sprint Worker"]
    end

    WA -->|Baileys WebSocket| Channel
    SL -->|Events API| Channel
    DC -->|Gateway| Channel
    WEB -->|HTTP/SSE| Channel

    CM -->|channel_read| MAIN
    CM -->|channel_reply| MAIN
    CG -->|Semantic search| MAIN
    GC -->|Knowledge extraction| MAIN

    MAIN <-->|Pair protocol| PAIR
    MAIN -->|Spawn workers| RSI
```

## Quality Gate Pipeline

```mermaid
graph LR
    subgraph Input["Tool Call"]
        TC["Edit / Write / Bash"]
    end

    subgraph PreTool["PreToolUse Gates"]
        direction TB
        DRY["DRY Check<br/>(DENY)"]
        KISS["KISS Check<br/>(DENY)"]
        CC_GATE["CC Check<br/>(WARN/DENY)"]
        DEP["Dependency Guard<br/>(DENY)"]
        SAFETY["Safety Gates<br/>(DENY)"]
    end

    subgraph PostTool["PostToolUse Gates"]
        direction TB
        BIGO["BIG-O Advisory"]
        YAGNI["YAGNI Advisory"]
        SOLID["SOLID Advisory"]
        GRASP["GRASP Advisory"]
    end

    subgraph Output["Result"]
        ALLOW["Tool Executes"]
        BLOCK["Tool Blocked"]
        WARN["Warning Emitted"]
    end

    TC --> PreTool
    DRY -->|DENY| BLOCK
    KISS -->|DENY| BLOCK
    CC_GATE -->|DENY| BLOCK
    DEP -->|DENY| BLOCK
    SAFETY -->|DENY| BLOCK

    DRY -->|ALLOW| ALLOW
    KISS -->|ALLOW| ALLOW
    CC_GATE -->|WARN| WARN
    WARN --> ALLOW

    ALLOW --> PostTool
    PostTool --> Output
```

## Data Flow

```mermaid
graph TB
    subgraph Session["Active Session"]
        CONV["Conversation"]
        TOOLS["Tool Calls"]
    end

    subgraph Learning["Learning Pipeline"]
        INSIGHTS["learning/insights.jsonl"]
        HYPO["learning/hypotheses.jsonl"]
        HEARTBEAT["learning/heartbeat.jsonl"]
    end

    subgraph Knowledge["Knowledge Base"]
        KONO_MEM["memory/MEMORY.md<br/>(Index)"]
        KONO_FILES["memory/*.md<br/>(Memory Files)"]
        EMBED_DB["embeddings.db<br/>(Vector Search)"]
    end

    subgraph Evolution["Evolution Engine"]
        GENOME_DATA["data/genomes/<br/>(DNA)"]
        EVO["/evo Pipeline"]
        CUBE["CUBE Dimensions"]
    end

    subgraph Persistence["Session State"]
        INST["instances/*.json"]
        CTX["rules/session-context.md"]
        PLANS["plans/*.md"]
    end

    CONV --> INSIGHTS
    CONV --> HYPO
    TOOLS --> HEARTBEAT
    INSIGHTS --> EVO
    EVO --> GENOME_DATA
    EVO --> CUBE
    HYPO -->|Verify| EVO
    CONV --> KONO_MEM
    KONO_MEM --> KONO_FILES
    KONO_FILES --> EMBED_DB
    CONV --> INST
    INST --> CTX
    CTX -->|Next session| CONV
```

## Component Counts

| Component | Count | Location |
|-----------|-------|----------|
| Playbooks (skills) | 212 | `skills/*/SKILL.md` |
| Workers (agents) | 39 | `agents/*.md` |
| Presets (modes) | 30 | `modes/definitions/` |
| Quality gates (hooks) | 39 | `hooks/*.py` |
| Commands | 67 | `commands/*.md` |
| Python libraries | 389 | `lib/*.py` |
| MCP servers | 5 | `mcp-servers/` |
| Rules files | 5 | `rules/*.md` |

## Key Design Principles

- **SOLID, GRASP, DRY, KISS, YAGNI, YSH, BIG-O** — enforced mechanically via hooks
- **Session continuity** — instance serialisation + context compression
- **Hypothesis-driven** — every improvement registers a falsifiable prediction
- **External scoring** — never self-score; use multi-model council
- **Plain language** — all output human-readable without GRIP knowledge
