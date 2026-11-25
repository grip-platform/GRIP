---
name: layered-refactoring
description: >
  Systematic layered refactoring methodology for legacy codebases.
  Refactor one architectural layer per PR in dependency order:
  config -> naming -> crypto -> data -> transport.
  Extracted from K>> (Arnold)'s SchipOTP PR chain.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /layered-refactor
  - "refactor legacy"
  - "systematic refactoring"
  - "layered refactoring"
see_also:
  - design-principles
  - code-agentic
origin:
  team_member: "K>>"
  project: SchipOTP
  evidence: "PRs #2-#7, 463 lines legacy -> clean modules"
---

# Layered Refactoring Methodology

Systematic approach to refactoring legacy codebases by decomposing changes
into architectural layers, each addressed in a separate PR.

---

## 1. Core Principle

**One architectural layer per PR.** Each PR touches exactly one concern,
builds on the previous, and leaves the codebase in a working state.

---

## 2. Layer Ordering (Dependency Chain)

Refactor in this order — each layer depends only on completed layers:

| Order | Layer | What Changes | Example |
|-------|-------|-------------|---------|
| 1 | **Config** | Settings, env vars, constants | Extract hardcoded values to config |
| 2 | **Naming** | Identifiers, module names, paths | Rename to domain language |
| 3 | **Crypto** | Security, hashing, encryption | Replace weak algorithms |
| 4 | **Data** | Models, schemas, storage | Repository pattern, migrations |
| 5 | **Transport** | APIs, protocols, I/O | Adapter pattern for pluggability |

### Why This Order

- Config changes break nothing downstream
- Naming after config avoids double-rename
- Crypto before data ensures secure storage from the start
- Data before transport ensures clean internal model before exposing it
- Transport last because it depends on all internal layers

---

## 3. PR Discipline

### Size Constraints
- Target: 100-300 lines changed per PR
- Hard ceiling: 500 lines (split if larger)
- Each PR must compile, pass tests, and be independently reviewable

### Dependency Chain
- PRs reference their predecessor: "Builds on #N"
- Never merge out of order
- If PR #3 needs a change from PR #1, update #1 first

### Commit Style
- One commit per logical change within the layer
- Prefix: `refactor(layer): description`
- Example: `refactor(data): extract repository pattern for OTP storage`

---

## 4. Patterns by Layer

### Data Layer: Repository Pattern
```
class OTPRepository:
    """Abstract storage interface."""
    def store(self, otp: OTP) -> None: ...
    def retrieve(self, key: str) -> Optional[OTP]: ...

class SqliteOTPRepository(OTPRepository):
    """Concrete SQLite implementation."""
```

### Transport Layer: Factory Pattern
```
class AdapterFactory:
    """Pluggable adapter creation."""
    _registry: Dict[str, Type[Adapter]] = {}

    @classmethod
    def register(cls, name: str, adapter: Type[Adapter]):
        cls._registry[name] = adapter

    @classmethod
    def create(cls, name: str, **kwargs) -> Adapter:
        return cls._registry[name](**kwargs)
```

---

## 5. Legacy Elimination Checklist

Before starting:
- [ ] Map all legacy code paths (grep for deprecated patterns)
- [ ] Identify dead code (unreachable branches, unused imports)
- [ ] Count lines to eliminate (target: track reduction per PR)

During:
- [ ] Each PR eliminates at least some legacy code
- [ ] No new legacy patterns introduced
- [ ] Test coverage maintained or improved

After:
- [ ] Zero legacy patterns remaining
- [ ] Module boundaries clean (no cross-layer imports)
- [ ] Document architecture decisions in ADR or CLAUDE.md

---

## 6. Trade-offs vs Comprehensive PRs

| Approach | Pros | Cons |
|----------|------|------|
| **Layered (K>>)** | Easy review, low risk, clear rollback | More PRs, slower merge cycle |
| **Comprehensive (A>>)** | Fewer PRs, faster delivery | Harder review, higher risk |

**When to use layered**: Legacy codebases, shared repos, unfamiliar code.
**When to use comprehensive**: Greenfield features, solo developer, well-tested code.
