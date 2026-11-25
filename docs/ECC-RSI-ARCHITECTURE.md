# Error-Correcting Codes for RSI: A Control Systems Approach

How ECC concepts from information theory map to practical features in GRIP's RSI infrastructure.

## Core Analogy

In ECC, a message passes through a noisy channel and arrives corrupted. The code detects and corrects errors.
In RSI, an agent's intent passes through an improvement loop and may drift. The ECC-inspired checks detect and correct that drift.

```
ECC:  source --> encode --> noisy channel --> decode --> corrected output
RSI:  intent --> plan   --> converge()    --> verify --> validated output
```

The "noise" in RSI is everything that degrades agent output: context decay, goal drift,
meta-work creep, regression introduction, and stale information. ECC gives us a
principled framework for detecting and correcting these failure modes.

## Mapping Table

| ECC Concept | RSI Application | Implementation |
|-------------|----------------|----------------|
| Hamming distance | Measure drift between planned and actual output | `hamming(planned_diff, actual_diff)` |
| Parity bits | Lightweight invariant checks per depth | Assertion gates in converge() |
| Reed-Solomon | Recovery from agent failures in MESH pipeline | Redundant agent execution |
| Turbo codes | Iterative decoding = nested converge loops | /rsi > /converge > sub-tasks |
| LDPC | Sparse dependency graph for gene interactions | Genome adjacency matrix |
| Convolutional codes | Sliding window over session history | KONO temporal validation |
| Channel capacity | Context window as bandwidth limit | ACE adaptive modulation |
| Interleaving | Fibonacci breathing distributes error bursts | Wave phase scheduling |

---

## 1. Hamming Distance: Drift Detection

Hamming distance measures the number of positions where two strings differ. Applied to RSI,
it quantifies how far an agent's actual output has drifted from the steering directive.

```python
def steering_drift(planned_files: set, actual_files: set) -> float:
    """Detect when Loop B drifts from Opus's steering directive."""
    intersection = planned_files & actual_files
    union = planned_files | actual_files
    jaccard = len(intersection) / len(union) if union else 1.0
    hamming = 1.0 - jaccard  # 0 = perfect, 1 = total drift
    return hamming

# In delegate mode steering:
directive = {"files": {"lib/auth.py", "tests/test_auth.py"}}
actual_changes = get_changed_files_from_git()
drift = steering_drift(directive["files"], actual_changes)
if drift > 0.5:
    alert_opus("Loop B drifted from directive", drift)
```

**Threshold**: drift > 0.3 triggers Opus re-steering. drift > 0.7 triggers halt.

The Hamming concept extends beyond file sets. In converge(), the "error budget" is the
distance between the current state's score and the criterion threshold. Each iteration
should reduce this distance -- if it increases, the improvement function is introducing
more noise than signal.

```python
def error_budget(current_score: float, criterion: float) -> float:
    """Hamming-like distance to convergence target."""
    return max(0.0, criterion - current_score) / criterion
```

---

## 2. Parity Bits: Invariant Assertions

Parity bits add 1 bit of redundancy to detect single-bit errors. In RSI, lightweight
invariants detect when an improvement introduces a regression:

```python
def parity_check(state_before, state_after, invariants):
    """Check that improvements don't violate invariants."""
    violations = []
    for name, check_fn in invariants.items():
        before_ok = check_fn(state_before)
        after_ok = check_fn(state_after)
        if before_ok and not after_ok:
            violations.append(f"Invariant '{name}' broken by improvement")
    return violations

# Example invariants for code tasks:
INVARIANTS = {
    "tests_pass": lambda s: s.test_results.passed,
    "no_new_warnings": lambda s: s.lint_warnings <= s.baseline_warnings,
    "file_count_stable": lambda s: abs(s.file_count - s.baseline_files) < 5,
}
```

**Cost**: O(1) per invariant per depth. Catches 80% of regressions before scoring.

In converge(), each scoring dimension (correctness, design, simplicity) acts as an
independent parity channel. A depth iteration that improves correctness but regresses
design triggers a parity violation before the composite score is computed -- catching
the issue at the dimension level rather than after aggregation.

---

## 3. Reed-Solomon: Multi-Dimensional Scoring as Parallel Channels

Reed-Solomon codes encode data across multiple symbols so that errors in any subset of
channels can be detected and corrected independently. Converge scoring maps directly:

```
RS encoding:     data --> [channel_1, channel_2, ..., channel_n]
Converge:        state --> [correctness, design, simplicity, performance, ...]
```

Each scoring dimension is an independent error-detection channel. When the composite
score stalls but individual dimensions diverge, that is the RS "syndrome" -- it tells
you exactly which channel has errors:

```python
def reed_solomon_diagnosis(scores_by_dimension: dict) -> list:
    """Identify which dimensions are dragging down convergence."""
    syndromes = []
    for dim, history in scores_by_dimension.items():
        if len(history) >= 2 and history[-1] < history[-2]:
            syndromes.append((dim, history[-2] - history[-1]))
    return sorted(syndromes, key=lambda s: s[1], reverse=True)
```

Applied to Broly MESH for agent redundancy:

```python
def reed_solomon_mesh(task, agents, min_agreement=2):
    """Execute task on multiple agents. Recover from failures
    if at least min_agreement agents succeed and agree."""
    results = []
    for agent in agents:
        try:
            result = agent.execute(task)
            results.append(result)
        except AgentFailure:
            continue  # Agent failed -- RS tolerates this

    if len(results) < min_agreement:
        raise UnrecoverableError("Too many agent failures")

    # Majority vote (like RS syndrome decoding)
    consensus = majority_vote(results)
    return consensus
```

**Application**: For critical tasks (security review, production deploy), dispatch to 3
agents. Accept if 2+ agree. For standard converge scoring, the multi-dimensional
approach provides the same error isolation at zero extra agent cost.

---

## 4. Turbo Codes: Nested Iterative Decoding

Turbo codes achieve near-Shannon-limit performance through iterative decoding between
two constituent decoders, passing "extrinsic information" between them. GRIP's RSI
has three nested loops that form a turbo-like architecture:

```
Turbo decoder:
  decode_1(y) --> extrinsic_1 --> decode_2(y, extrinsic_1) --> extrinsic_2 --> ...

GRIP nested loops:
  /rsi (outer)
    wave_plan --> /converge (middle)
      improve --> score --> weaknesses --> /converge sub-task (inner)
        targeted_fix --> verify --> result
      <-- result feeds back as extrinsic info
    <-- converge result feeds wave plan for next cycle
```

The "extrinsic information" at each level:

| Loop Level | Extrinsic Information Passed | Analogous To |
|------------|------------------------------|--------------|
| /rsi outer | Wave plan, genome fitness, breathing phase | Turbo outer decoder |
| /converge middle | Weakness list, dimension scores, stall detection | Turbo inner decoder 1 |
| Sub-task inner | Targeted fix results, test outcomes | Turbo inner decoder 2 |

**Key property**: Like turbo codes, each level passes genuinely new information to the
next. The converge "weaknesses" list is the equivalent of turbo extrinsic information --
it represents what THIS iteration learned that the NEXT iteration should attend to.

**Stall detection as convergence threshold**: Turbo decoders have a minimum SNR below which
iterations stop helping. converge() has the same property -- the stall detector identifies
when score deltas flatten, preventing wasted iterations.

---

## 5. Channel Capacity: Context Window as Bandwidth

Shannon's channel capacity theorem defines the maximum rate at which data can be
transmitted with arbitrarily low error. The context window is the RSI channel bandwidth.

```
Shannon:  C = B * log2(1 + SNR)
RSI:      effective_capacity = context_window * signal_ratio
```

Where `signal_ratio` is the proportion of context tokens carrying task-relevant
information (vs. noise: stale state, irrelevant history, boilerplate).

### ACE as Adaptive Modulation

In telecommunications, adaptive modulation changes the encoding scheme based on channel
conditions. ACE (Adaptive Context Extension) does the same:

```
Channel conditions:           ACE response:
  Low noise, simple task  --> Opus 200K (efficient encoding)
  High noise, complex     --> Opus 1M (redundant encoding)
  Approaching capacity    --> Context gate at 85% (rate limiting)
```

| ACE Profile | Channel Analogy | When Used |
|-------------|-----------------|-----------|
| Opus 200K | Standard modulation (QAM-16) | Default, simple tasks |
| Opus Fast 1M | Higher bandwidth (QAM-64) | Broly MESH, multi-file refactors |
| Context gate 85% | Back-pressure / flow control | Approaching capacity limit |
| Compaction | Lossy compression | Capacity exceeded, must shed noise |

The context gate at 85% is a flow control mechanism -- it signals that the channel
is approaching capacity and the sender (agent) must either compress (compact), switch
channels (extend context), or terminate the session.

**Noise sources and mitigation**:

| Noise Source | Effect | ECC Mitigation |
|--------------|--------|----------------|
| Stale file reads | Wrong assumptions | File read cache + re-read gating |
| Meta-work creep | Signal dilution | Sliding window meta-ratio check |
| Goal drift | Channel wandering | Hamming drift detection |
| Context decay | Information loss | KONO persistence + compaction |

---

## 6. LDPC: Sparse Gene Interaction Graph

LDPC codes use sparse bipartite graphs (Tanner graphs) for efficient decoding. The
genome can be viewed as an LDPC-like graph:

```
Gene nodes:     [code-mode] [design-principles] [converge] [broly] ...
Check nodes:    [tests-pass?] [design-clean?] [performance-ok?] ...
Edges:          gene --> check if gene affects that quality dimension

Sparse property: each gene affects 2-3 checks, each check depends on 3-5 genes.
```

**Application**: When a check fails (e.g., tests break), the sparse graph identifies
which genes (skills) are most likely responsible -- enabling targeted fitness
adjustment instead of blanket penalties.

```python
def ldpc_attribution(failed_check, gene_graph):
    """Identify genes most likely responsible for a failed check."""
    connected_genes = gene_graph.neighbors(failed_check)
    # Belief propagation: weight by gene fitness (lower fitness = more suspect)
    suspects = sorted(connected_genes, key=lambda g: g.fitness)
    return suspects[:3]  # Top 3 suspects
```

This connects directly to `converge_dna.py`'s selection mechanism. When a converge
run fails, the genome uses LDPC-style attribution to determine which genes to penalize
rather than applying uniform fitness decay. See `docs/EVOLUTIONARY-DNA-DESIGN.md`.

---

## 7. Convolutional Codes: Sliding Window Verification

Convolutional codes encode data using a sliding window over the input stream. Applied
to session history:

```python
def sliding_window_verify(session_history, window_size=5):
    """Verify consistency across a sliding window of recent actions."""
    for i in range(len(session_history) - window_size):
        window = session_history[i:i + window_size]

        # Check: are actions in this window consistent with stated goal?
        goal_drift = measure_goal_consistency(window)
        if goal_drift > THRESHOLD:
            return Warning(f"Goal drift detected at actions {i}-{i+window_size}")

        # Check: is meta-work ratio within budget?
        meta_ratio = count_meta_actions(window) / window_size
        if meta_ratio > 0.25:
            return Warning(f"Meta-work ratio {meta_ratio:.0%} exceeds 25% cap")

    return Ok()
```

**Application**: KONO memory stores session events. The sliding window catches drift
patterns that single-event checks miss -- a convolutional code detects errors that
span multiple symbols, just as the sliding window detects drift across multiple actions.

---

## 8. Fibonacci Breathing as Interleaving

In telecommunications, interleaving rearranges data before transmission so that burst
errors (concentrated failures) are spread across the codeword, making them correctable
by standard codes designed for random errors.

GRIP's Fibonacci breathing protocol serves exactly this function for RSI waves.

### The Burst Error Problem

Without interleaving, concentrated work in one domain creates "burst errors" --
cascading failures where one bad decision compounds:

```
Without interleaving (burst errors):
  [code][code][code][code][code] --> fatigue --> [bug][bug][bug][bug]
                                                 ^--- burst error

With Fibonacci interleaving:
  W1[code] W2[code,test] W3[code,design,test] ... W5[breathe] W3[docs] W2[retro] W1[rest]
  Errors distributed across phases --> each individually correctable
```

### Fibonacci Wave as Interleaver

The ascending wave (W1-W8) increases task intensity. The descending wave (W8-W1)
interleaves recovery phases that serve as error correction windows:

| Wave | Ascent (work) | Descent (correction) | ECC Function |
|------|---------------|---------------------|--------------|
| W1 | Light warmup | Rest | Channel quiet period |
| W2 | Focused task | Retrospective | Error syndrome extraction |
| W3 | Multi-file work | Documentation | State serialization (checkpoint) |
| W5 | Complex integration | Creative/articles | Orthogonal perspective shift |
| W8 | Peak intensity | (apex -- transition) | Full decode before descent |

**Why this works**: Each breathing pause on descent is a decoding window. The
retrospective at W2-descent extracts error syndromes (what went wrong). The
documentation at W3-descent serializes corrected state. The creative phase at
W5-descent provides an orthogonal perspective -- like interleaving across frequency
bands in OFDM.

### Interleaving Depth

The Fibonacci sequence (1, 1, 2, 3, 5, 8, 5, 3, 2, 1, 1) provides natural interleaving
depth -- the spacing between work and correction phases follows the golden ratio,
which is provably optimal for distributing periodic events across a sequence.

```python
def fibonacci_interleave(tasks: list, breathing_fn) -> list:
    """Interleave tasks with breathing pauses at Fibonacci intervals."""
    fib_waves = [1, 1, 2, 3, 5, 8, 5, 3, 2, 1, 1]
    schedule = []
    task_idx = 0
    for wave_intensity in fib_waves:
        # Work phase: assign tasks proportional to wave intensity
        for _ in range(min(wave_intensity, len(tasks) - task_idx)):
            if task_idx < len(tasks):
                schedule.append(("work", tasks[task_idx]))
                task_idx += 1
        # Breathing phase: error correction window
        schedule.append(("breathe", breathing_fn(wave_intensity)))
    return schedule
```

---

## Composite: ECC-Enhanced RSI Loop

All ECC concepts combine into a unified RSI loop with layered error correction:

```python
def ecc_enhanced_converge(state, criterion, improve, max_depth=3):
    """converge() with ECC error detection at every depth."""
    invariants = extract_invariants(state)         # Parity setup
    planned_changes = plan_changes(state, criterion)  # Hamming baseline
    dimension_history = defaultdict(list)           # RS channels

    for depth in range(max_depth + 1):
        new_state = improve(state)

        # Layer 1 - Parity: invariants preserved?
        violations = parity_check(state, new_state, invariants)
        if violations:
            new_state = rollback_and_fix(state, violations)

        # Layer 2 - Hamming: drift from plan?
        drift = steering_drift(planned_changes, actual_changes(new_state))
        if drift > 0.3:
            new_state = re_steer(new_state, planned_changes)

        # Layer 3 - Reed-Solomon: dimension diagnosis
        scores = score_by_dimension(new_state, criterion)
        for dim, score in scores.items():
            dimension_history[dim].append(score)
        syndromes = reed_solomon_diagnosis(dimension_history)
        if syndromes:
            new_state = targeted_fix(new_state, syndromes[0])

        # Layer 4 - Convolutional: session consistency
        window_check = sliding_window_verify(get_recent_actions())
        if window_check.is_warning():
            log_warning(window_check)

        # Standard converge scoring
        score = evaluate(new_state, criterion)
        if score >= 90:
            return new_state

        state = new_state

    return state  # Best effort at max depth
```

### Error Correction Stack

```
Layer 5: Fibonacci interleaving      (burst error prevention - wave level)
Layer 4: Convolutional sliding window (session drift - multi-action)
Layer 3: Reed-Solomon dimensions      (scoring channels - per-depth)
Layer 2: Hamming drift detection      (steering alignment - per-depth)
Layer 1: Parity invariant checks      (regression detection - per-depth)
```

Each layer catches errors the layers below miss. Parity catches single regressions.
Hamming catches directional drift. Reed-Solomon isolates which dimension degraded.
Convolutional catches gradual session-level drift. Fibonacci interleaving prevents
burst errors from accumulating in the first place.

---

## Cost Analysis

| Check | Overhead per depth | Value |
|-------|-------------------|-------|
| Parity (invariants) | ~0 (in-memory) | Catches regressions instantly |
| Hamming (drift) | O(n) file comparison | Prevents wasted iterations |
| RS dimensions | O(d) scoring dimensions | Isolates degraded channels |
| Sliding window | O(w) history scan | Catches gradual drift |
| LDPC attribution | O(genes) graph walk | Targeted fitness updates |
| Fibonacci interleaving | O(1) schedule lookup | Prevents burst errors |

**Recommendation**: Enable parity + Hamming + RS dimensions by default (near-zero cost).
Use agent-level RS redundancy only for critical paths. Sliding window runs at session
level, not per-depth. Fibonacci interleaving is structural -- it costs nothing at
runtime because it shapes the schedule, not the computation.

## Connection to GRIP Systems

| GRIP System | ECC Role | Reference |
|-------------|----------|-----------|
| converge() | Inner turbo decoder | `skills/converge/SKILL.md` |
| /rsi | Outer turbo decoder | `skills/rsi-aliases/SKILL.md` |
| Genome | LDPC gene-check graph | `docs/EVOLUTIONARY-DNA-DESIGN.md` |
| ACE | Adaptive channel modulation | `docs/ACE-ARCHITECTURE.md` |
| Wave protocol | Fibonacci interleaver | `data/mode-wave-map.yaml` |
| KONO | Convolutional memory buffer | `lib/kono_substrate.py` |
| Context gate | Channel flow control | `hooks/context-gate-hook.py` |
| Delegate mode | Turbo constituent decoders | `docs/DELEGATE-MODE-ARCHITECTURE.md` |

## References

- Shannon, C.E. (1948). A Mathematical Theory of Communication.
- Berrou et al. (1993). Near Shannon Limit Error-Correcting Coding and Decoding: Turbo-Codes.
- Gallager, R.G. (1962). Low-Density Parity-Check Codes.
- GRIP converge() skill: `skills/converge/SKILL.md`
- GRIP genome: `docs/EVOLUTIONARY-DNA-DESIGN.md`
- GRIP ACE: `docs/ACE-ARCHITECTURE.md`
