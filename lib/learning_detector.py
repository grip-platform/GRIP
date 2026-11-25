#!/usr/bin/env python3
"""
Autonomous Learning Engine - Learning Event Detector

DIALECTICAL FRAMEWORK:
  Thesis:     Traditional ML - predict next token
  Antithesis: GRIP Learning - recognise patterns, name them, generalise
  Synthesis:  Auto-generate skills when novelty detected

LEARNING TRIGGERS:
  1. High novelty score (>0.4) from embedding hook
  2. Pattern recognition (naming something new)
  3. Generalisation signal (specific solution becomes principle)
  4. Teaching moment (V>> correction with "you should have")

FLOW:
  detect_learning_event() -> evaluate_generalisability() -> propose_skill() -> notify_user()

V>> maintains oversight: Auto-detect + propose + await confirmation.

Source: Instance dbca9c2d, Gen 50, 2025-12-21
        Merged from website project session (YSH discovery)
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from json_utils import safe_load_json, safe_load_jsonl
from telemetry_shape import is_evo_telemetry, is_friction_telemetry
from claude_dir import get_claude_dir
from insight_text import clean_insight_text, says_something, title_of

# Import embedding engine if available — W5 follow-up: dispatch via get_engine()
try:
    from embeddings import get_engine
    HAS_EMBEDDINGS = True
except ImportError:
    HAS_EMBEDDINGS = False

# Resolved through the shared shim so CLAUDE_DIR names the checkout /learn
# writes into. A bare Path.home() / ".claude" sent every write into the shared
# tree, so /learn run from a worktree rewrote two tracked files there
# (learning/insights.jsonl, rules/learned-insights.md) -- the
# shared_tree_writer_in_worktree class. Without CLAUDE_DIR the root is unchanged.
CLAUDE_DIR = get_claude_dir()
LEARNING_DIR = CLAUDE_DIR / "learning"
PENDING_DIR = LEARNING_DIR / "pending"
APPROVED_DIR = LEARNING_DIR / "approved"
REJECTED_DIR = LEARNING_DIR / "rejected"
RULES_DIR = CLAUDE_DIR / "rules"
METRICS_FILE = CLAUDE_DIR / "metrics.jsonl"
INSIGHTS_FILE = LEARNING_DIR / "insights.jsonl"


def _user_insights_path() -> Path:
    """Get user-scoped insights JSONL path."""
    try:
        from lib.user_detection import get_username
        username = get_username()
    except ImportError:
        username = "unknown"
    if username == "unknown":
        return INSIGHTS_FILE  # fall back to shared
    user_dir = LEARNING_DIR / "users" / username
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / "insights.jsonl"
LEARNED_RULES_FILE = RULES_DIR / "learned-insights.md"

# Learning event thresholds
NOVELTY_THRESHOLD = 0.4
PATTERN_THRESHOLD = 2  # Minimum trigger count to consider learning event
TEACHING_PATTERNS = [
    r"you should have",
    r"obvious enhancement",
    r"you missed",
    r"should've",
    r"better approach would be",
    r"correct way is",
    r"anti-pattern",
    r"don't do this",
    r"always use",
    r"never use",
    r"remember to",
    r"lesson learned",
]

# Pattern naming indicators (user coined a new term)
NAMING_PATTERNS = [
    r"let's call this",
    r"I'll name this",
    r"this is (?:called|known as)",
    r"the \w+ principle",
    r"the \w+ pattern",
    r"(?:YSH|YAGNI|DRY|KISS|SOLID|GRASP)",  # Established principles
    r"dialectical",
]

# Generalisation indicators
GENERALISATION_PATTERNS = [
    r"in general",
    r"as a rule",
    r"this applies to",
    r"universally",
    r"always works",
    r"pattern that",
    r"principle:",
    r"lesson:",
    r"takeaway:",
    r"generalis[ea]",  # generalise/generalize + derivatives
    r"paramount",
    r"critical learning",
    r"key insight",
    r"important pattern",
    r"cross-project",
    r"any (?:project|repo|codebase)",
]


def ensure_directories():
    """Create learning directory structure if not exists."""
    for directory in [LEARNING_DIR, PENDING_DIR, APPROVED_DIR, REJECTED_DIR]:
        directory.mkdir(parents=True, exist_ok=True)


def _fingerprint(text: str) -> str:
    """Normalized content hash for dedup. Strips noise, sorts words."""
    words = sorted(set(re.findall(r'\b\w{3,}\b', text.lower())))
    return hashlib.sha256(' '.join(words).encode()).hexdigest()[:16]


def _find_related(text: str, entries: list, threshold: float = 0.3) -> list:
    """Find related insight IDs by Jaccard word overlap."""
    words_new = set(re.findall(r'\b\w{3,}\b', text.lower()))
    if not words_new:
        return []
    related = []
    for entry in entries:
        words_existing = set(re.findall(r'\b\w{3,}\b', entry['text'].lower()))
        if not words_existing:
            continue
        jaccard = len(words_new & words_existing) / len(words_new | words_existing)
        if jaccard >= threshold:
            related.append(entry['id'])
    return related[:5]


def _load_insights() -> list:
    """Load all entries from insights.jsonl, normalising field names."""
    entries = safe_load_jsonl(INSIGHTS_FILE)
    for e in entries:
        if "text" not in e:
            e["text"] = (
                e.get("message") or e.get("description")
                or e.get("pattern") or ""
            )
        if "id" not in e:
            import hashlib
            raw = (e.get("text", "") + e.get("source", "")
                   + e.get("created_at", e.get("timestamp", "")))
            e["id"] = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return entries


def _save_insights(entries: list) -> None:
    """Atomic write of entries to insights.jsonl (shared + user-scoped)."""
    INSIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = INSIGHTS_FILE.with_suffix('.tmp')
    with open(tmp, 'w') as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    os.replace(tmp, INSIGHTS_FILE)

    # Dual-write to user-scoped path
    user_path = _user_insights_path()
    if user_path != INSIGHTS_FILE:
        try:
            user_path.parent.mkdir(parents=True, exist_ok=True)
            user_tmp = user_path.with_suffix('.tmp')
            with open(user_tmp, 'w') as f:
                for entry in entries:
                    f.write(json.dumps(entry) + "\n")
            os.replace(user_tmp, user_path)
        except OSError:
            pass


def _build_jaccard_adjacency(active: list, word_sets: dict) -> Dict[str, set]:
    """Pairwise Jaccard >= 0.5 adjacency over active entries."""
    import itertools
    ids = [e["id"] for e in active]
    adj: Dict[str, set] = {eid: set() for eid in ids}
    for id_a, id_b in itertools.combinations(ids, 2):
        ws_a, ws_b = word_sets[id_a], word_sets[id_b]
        union = ws_a | ws_b
        if union and len(ws_a & ws_b) / len(union) >= 0.5:
            adj[id_a].add(id_b)
            adj[id_b].add(id_a)
    return adj


def _extract_structures(active: list) -> Optional[dict]:
    """Predicate Structure per active insight via lib/smt/text_structure.py.

    None when the kernel is switched off (GRIP_SMT_RELATIONAL_SHIFT=0) or
    unavailable; the caller then falls back to Jaccard adjacency, loudly.
    """
    try:
        from smt import enabled
        if not enabled("RELATIONAL_SHIFT"):
            return None
        from smt.text_structure import extract_structure
        return {e["id"]: extract_structure(e["id"], e["text"]) for e in active}
    except Exception as exc:
        print(f"[WARN] structural pairing unavailable ({type(exc).__name__}: {exc}); "
              "Jaccard adjacency used", file=sys.stderr)
        return None


def _literal_pairing(sa, sb) -> Tuple[list, bool]:
    """Compare two insight Structures under the LITERAL mapping (entities
    correspond by identical name); return (shared relation names,
    contradiction present).

    Consolidation merges restatements of one lesson: the same relations over
    the same objects. GMap would gladly align "rebase causes conflicts" with
    "compaction causes amnesia" — a fine analogy, and exactly the wrong thing
    to merge — so the pairing is literal by construction. A same-named
    relation whose arguments disagree (a role flip, or a different object)
    is a contradiction: two lessons stated with the same words.
    """
    from smt.relational_shift import alignable_differences
    from smt.sme import Alignment, PredicateKind
    shared = {e.name for e in sa.entities} & {e.name for e in sb.entities}
    diffs = alignable_differences(sa, sb, Alignment(entity_map={e: e for e in shared}))
    contradicted = {d.predicate for d in diffs if d.kind in ("role_flip", "tier_flip")}
    b_names = {p.name for p in sb.predicates}
    relations = sorted(
        p.name for p in sa.predicates
        if p.kind == PredicateKind.RELATION and p.name in b_names
        and set(p.args) <= shared and p.name not in contradicted
    )
    return relations, bool(contradicted)


def _build_structural_adjacency(active: list, word_sets: dict, structs: dict) -> Dict[str, set]:
    """Adjacency by structural pairing (lib/smt/relational_shift.py).

    Two insights are adjacent when they share a literal relation, or when
    they are surface duplicates (Jaccard >= 0.5) whose aligned relations do
    not contradict. Jaccard alone merged role-flipped sentences built from
    the same words; alignable differences keep them apart. Pairs sharing no
    word are skipped: a literal relation needs shared objects, and shared
    objects are shared words.
    """
    import itertools
    ids = [e["id"] for e in active]
    adj: Dict[str, set] = {eid: set() for eid in ids}
    for id_a, id_b in itertools.combinations(ids, 2):
        ws_a, ws_b = word_sets[id_a], word_sets[id_b]
        common = ws_a & ws_b
        if not common:
            continue
        jaccard = len(common) / len(ws_a | ws_b)
        relations, contradicted = _literal_pairing(structs[id_a], structs[id_b])
        if relations or (jaccard >= 0.5 and not contradicted):
            adj[id_a].add(id_b)
            adj[id_b].add(id_a)
    return adj


def _build_adjacency(active: list, word_sets: dict, structs: Optional[dict]) -> Dict[str, set]:
    """Structural adjacency when the kernel is on, Jaccard otherwise; a
    structural failure falls back to Jaccard and says so."""
    if structs is None:
        return _build_jaccard_adjacency(active, word_sets)
    try:
        return _build_structural_adjacency(active, word_sets, structs)
    except Exception as exc:
        print(f"[WARN] structural pairing failed ({type(exc).__name__}: {exc}); "
              "Jaccard adjacency used", file=sys.stderr)
        return _build_jaccard_adjacency(active, word_sets)


def _cluster_rationale(structs: dict, cluster_ids: list) -> dict:
    """Why these insights merged, in the kernel's terms: the relation names
    every member states, and the cluster's attribute-to-relational share."""
    from smt.relational_shift import relational_shift
    from smt.sme import PredicateKind
    members = [structs[cid] for cid in cluster_ids]
    shared = set.intersection(*(
        {p.name for p in s.predicates if p.kind == PredicateKind.RELATION} for s in members
    ))
    return {"shared_relations": sorted(shared), "relational_shift": relational_shift(members)}


def _bfs_component(seed: str, adj: Dict[str, set], visited: set) -> list:
    """One BFS component starting at seed; mutates `visited`."""
    component: list = []
    queue = [seed]
    while queue:
        node = queue.pop(0)
        if node in visited:
            continue
        visited.add(node)
        component.append(node)
        queue.extend(adj[node] - visited)
    return component


def _find_clusters(ids: list, adj: Dict[str, set], min_size: int = 3) -> list:
    """Return connected components >= min_size."""
    visited: set = set()
    return [
        component
        for eid in ids
        if eid not in visited
        for component in [_bfs_component(eid, adj, visited)]
        if len(component) >= min_size
    ]


def _build_consolidated_entry(cluster_entries: list, cluster_ids: list) -> dict:
    """Compose a new consolidated insight from cluster members (longest as base)."""
    base = max(cluster_entries, key=lambda e: len(e["text"]))
    return {
        "id": hashlib.sha256(
            f"consolidated-{'|'.join(sorted(cluster_ids))}".encode()
        ).hexdigest()[:12],
        "text": base["text"],
        "context": base.get("context", ""),
        "tags": list({t for e in cluster_entries for t in e.get("tags", [])}),
        "fingerprint": _fingerprint(base["text"]),
        "seen_count": sum(e.get("seen_count", 1) for e in cluster_entries),
        "consolidated_from": cluster_ids,
        "related_to": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ingested": False,
    }


def _mark_consolidated(cluster_ids: list, by_id: dict, new_id: str) -> None:
    """Tag originals as consolidated into the new entry."""
    for cid in cluster_ids:
        by_id[cid]["consolidated"] = True
        by_id[cid]["consolidated_into"] = new_id


def consolidate_insights() -> Dict[str, Any]:
    """YSH 3x merge: consolidate 3+ insights that restate one lesson.

    Adjacency is structural pairing (lib/smt/relational_shift.py) with
    Jaccard >= 0.5 as the surface tier; pure Jaccard under
    GRIP_SMT_RELATIONAL_SHIFT=0. Each consolidated entry records the
    relations its members share and the cluster's relational-shift measure.

    Coordinator: load → structures → adjacency → cluster → build → mark → save.
    """
    entries = _load_insights()
    active = [e for e in entries if not e.get("consolidated")]
    if len(active) < 3:
        return {"consolidated": 0, "clusters": 0}

    word_sets = {
        e["id"]: set(re.findall(r'\b\w{3,}\b', e["text"].lower()))
        for e in active
    }
    structs = _extract_structures(active)
    adj = _build_adjacency(active, word_sets, structs)
    clusters = _find_clusters([e["id"] for e in active], adj)
    if not clusters:
        return {"consolidated": 0, "clusters": 0}

    by_id = {e["id"]: e for e in entries}
    consolidated_count = 0
    for cluster_ids in clusters:
        cluster_entries = [by_id[cid] for cid in cluster_ids]
        new_entry = _build_consolidated_entry(cluster_entries, cluster_ids)
        if structs is not None:
            new_entry.update(_cluster_rationale(structs, cluster_ids))
        entries.append(new_entry)
        _mark_consolidated(cluster_ids, by_id, new_entry["id"])
        consolidated_count += len(cluster_ids)

    _save_insights(entries)
    return {"consolidated": consolidated_count, "clusters": len(clusters)}


def _extract_rule_content(text: str) -> dict:
    """Extract structured When/Do/Don't from insight text."""
    result = {"when": "", "do": "", "dont": ""}

    if " -- " in text:
        parts = text.split(" -- ", 1)
        result["when"] = parts[0].strip()
        result["do"] = parts[1].strip()
    elif ":" in text:
        parts = text.split(":", 1)
        result["when"] = parts[0].strip()
        result["do"] = parts[1].strip()
    else:
        result["do"] = text.strip()

    # Derive Don't as inverse of Do. NB: only "always X" -> "never X" is sound.
    # The reverse ("never X" -> "always X") is UNSAFE — it turns a safety
    # imperative ("never evade the gate") into "always evade the gate", which
    # reads as "avoid always-evading" and wrongly licenses occasional evasion.
    # A genuine Don't for a "never X" Do is "X" itself, which a string-flip
    # cannot produce, so we synthesise NO Don't for the "never" case (an absent
    # Don't is safe; an inverted one is not).
    if result["do"]:
        do_lower = result["do"].lower()
        if "always" in do_lower:
            result["dont"] = result["do"].replace("always", "never").replace("Always", "Never")
        elif "halt" in do_lower or "stop" in do_lower:
            result["dont"] = "Continue without asking"

    return result


def _is_valid_rule(content: dict) -> bool:
    """Validate rule has actionable content (not empty template)."""
    if not content.get("do", "").strip():
        return False
    do_text = content["do"].strip()
    if len(do_text) < 10:
        return False
    if any(p in do_text.lower() for p in ["{{", "todo", "placeholder", "tbd"]):
        return False
    # A Do that is only a removed payload or path says nothing (GRIP#8068).
    if not says_something(do_text):
        return False
    return True


def _collect_rules_from_approved() -> list:
    """Source 1: approved skill candidates."""
    APPROVED_DIR.mkdir(parents=True, exist_ok=True)
    rules: list = []
    for candidate_file in sorted(APPROVED_DIR.glob("*.json")):
        candidate = safe_load_json(candidate_file)
        if candidate is None:
            continue
        rules.append({
            "title": candidate.get("skill_name", "unnamed").replace("-", " ").title(),
            "text": candidate.get("full_content", candidate.get("description", "")),
            "score": candidate.get("learning_score", 0),
            "seen": 1,
            "source_id": candidate.get("id", ""),
            "source_type": "approved_candidate",
        })
    return rules


def _collect_rules_from_insights() -> list:
    """Source 2: proven insights (seen_count >= 3, not consolidated away)."""
    rules: list = []
    for entry in _load_insights():
        if entry.get("consolidated") or entry.get("seen_count", 1) < 3:
            continue
        # Rows written before GRIP#8068 can carry a tool payload or a path as their
        # text; cleaned here, so the rendered rule never does, and titled at a word,
        # never mid-string. The body is never cut (only the title is), so a long
        # insight keeps its conclusion (Codex on GRIP#8080).
        text = clean_insight_text(entry["text"], limit=None)
        rules.append({
            "title": title_of(text),
            "text": text,
            "score": entry.get("learning_score", 0),
            "seen": entry.get("seen_count", 1),
            "source_id": entry.get("id", ""),
            "source_type": "proven_insight",
        })
    return rules


def _render_rule_section(rule: dict) -> Optional[List[str]]:
    """Render one rule as markdown lines; return None if rule is hollow."""
    content = _extract_rule_content(rule["text"])
    if not _is_valid_rule(content):
        print(f"[WARN] Dropping hollow rule: {rule['title']}", file=sys.stderr)
        return None
    section = [f"## {rule['title']}", ""]
    if content["when"]:
        section.append(f"**When**: {content['when']}")
    section.append(f"**Do**: {content['do']}")
    if content["dont"]:
        section.append(f"**Don't**: {content['dont']}")
    section.append(f"Source: {rule['source_id']} | Score: {rule['score']:.2f} | Seen: {rule['seen']}x")
    section.append("")
    return section


def _render_rules_markdown(rules: list) -> List[str]:
    """Compose the full markdown document for learned-insights."""
    lines = [
        "# Learned Insights (Auto-Generated)",
        "",
        "Rules derived from approved learning insights. Regenerated by `/learn`.",
        "DO NOT edit manually -- changes will be overwritten.",
        "",
    ]
    for rule in rules:
        section = _render_rule_section(rule)
        if section is not None:
            lines.extend(section)
    return lines


def _validate_written_rules(path: Path) -> int:
    """Post-write hollow-rule audit; returns count of hollow sections."""
    written = path.read_text()
    sections = re.findall(r'^## .+\n(.*?)(?=^## |\Z)', written, re.MULTILINE | re.DOTALL)
    hollow = sum(1 for s in sections if '**Do**:' not in s or len(s.strip()) < 20)
    if hollow > 0:
        print(f"[WARN] {hollow} hollow rule(s) detected after generation", file=sys.stderr)
    return hollow


def generate_learned_rules() -> Dict[str, Any]:
    """Generate rules/learned-insights.md from approved candidates + proven insights.

    Coordinator: collect from 2 sources → cap → render → atomic write → audit.
    """
    rules = _collect_rules_from_approved() + _collect_rules_from_insights()
    # Chokepoint: never render friction TELEMETRY as a "learned rule" — both
    # /evo friction-frequency aggregates (is_evo_telemetry) AND auto-captured
    # guardrail/gate block records (is_friction_telemetry). Both are
    # measurements, not lessons: insights.jsonl holds ~1800 /evo: aggregates
    # with high seen counts + duplicate facts, and block records ("Bash
    # execution error [guardrail_...]") whose safety "never" text can invert to
    # a dangerous "always". Without this they crowd out and duplicate real
    # insights in the always-loaded rules/learned-insights.md.
    rules = [r for r in rules
             if not is_evo_telemetry(r.get("text", ""))
             and not is_friction_telemetry(r.get("text", ""))]
    # Hollow rules are dropped BEFORE the cap, so a payload-only legacy row with a
    # high score never takes one of the 30 slots from a real rule, and the count
    # returned is the count rendered (Codex on GRIP#8080).
    rules = [r for r in rules if _is_valid_rule(_extract_rule_content(r["text"]))]
    if not rules:
        if LEARNED_RULES_FILE.exists():
            LEARNED_RULES_FILE.unlink()
        return {"rules_written": 0}
    rules.sort(key=lambda r: (r["score"], r["seen"]), reverse=True)
    rules = rules[:30]
    lines = _render_rules_markdown(rules)
    RULES_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LEARNED_RULES_FILE.with_suffix('.tmp')
    tmp.write_text('\n'.join(lines))
    os.replace(tmp, LEARNED_RULES_FILE)
    _validate_written_rules(LEARNED_RULES_FILE)
    return {"rules_written": len(rules)}


def _mesh_broadcast(text: str, context: str, tags: list, fingerprint: str) -> None:
    """Real-time cross-machine propagation of a locally-captured insight.

    Fail-soft. Only fires for NEW local insights (called from the new-entry
    branch); the bridge's own origin guard is a second loop-prevention layer.
    See lib/mesh_insight_bridge.py.

    The text and context are scrubbed whole by lib/review_redaction.py first,
    because the bridge's own shield fails open (issue #3820). When the shield
    cannot run, the insight is not broadcast; it stays on this machine.
    """
    try:
        from review_redaction import shield_text
        text, context = shield_text(text), shield_text(context)
    except Exception:  # noqa: BLE001 -- without a shield, nothing is sent
        print("learning_detector: redaction shield unavailable, insight not broadcast",
              file=sys.stderr)
        return
    try:
        from mesh_insight_bridge import broadcast_insight
        broadcast_insight(text=text, context=context, tags=tags, fingerprint=fingerprint)
    except Exception:
        pass


def persist_insight(insight_text: str, context: str = "", tags: List[str] = None) -> str:
    """Persist an agent insight to JSONL for cross-session access.

    Agent-generated observations (Insight blocks, error discoveries,
    gate interactions) are written here so /learn can pick them up.
    Deduplicates by content fingerprint — repeated insights increment
    seen_count instead of creating new entries. New entries get
    cross-referenced via Jaccard word overlap.

    Returns:
        The 12-char hex ID of the persisted (or existing) insight.
    """
    fp = _fingerprint(insight_text)
    entries = _load_insights()

    # Check for fingerprint match (dedup)
    for entry in entries:
        if entry.get("fingerprint") == fp:
            entry["seen_count"] = entry.get("seen_count", 1) + 1
            entry["last_seen_at"] = datetime.now(timezone.utc).isoformat()
            _save_insights(entries)
            return entry["id"]

    # New entry — compute cross-references
    new_entry = {
        "id": hashlib.sha256(
            f"{insight_text}{datetime.now().isoformat()}".encode()
        ).hexdigest()[:12],
        "text": insight_text,
        "context": context,
        "tags": tags or [],
        "fingerprint": fp,
        "seen_count": 1,
        "related_to": _find_related(insight_text, entries),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ingested": False,
    }
    entries.append(new_entry)
    _save_insights(entries)
    _mesh_broadcast(insight_text, context, tags or [], fp)
    return new_entry["id"]


def ingest_agent_insight(
    insight_text: str,
    context: str = "",
    source: str = "session"
) -> Dict[str, Any]:
    """Feed agent-generated insight into the learning pipeline.

    Unlike detect_learning_event() which processes USER messages,
    this accepts AGENT observations and evaluates them for learning
    potential using the same scoring infrastructure.

    Args:
        insight_text: The insight content (e.g. from a Insight block)
        context: Where the insight came from (error, gate, pattern)
        source: Category - "session", "error_recovery", "gate_interaction"

    Returns:
        Dict with detection results, enriched with source metadata.
    """
    # Reuse existing scoring — agent insights get inherent novelty (self-discovered)
    result = detect_learning_event(
        message=insight_text,
        novelty_score=0.6,
    )

    # Agent insights are always teaching_moment + generalisation when they score well
    if result["learning_score"] > 0.3:
        result["triggers"]["teaching_moment"] = True
        result["triggers"]["generalisation"] = True
        # Recalculate score to reflect forced triggers
        result["learning_score"] = round(calculate_learning_score(
            0.6, True, result["triggers"]["new_term"], True
        ), 4)
        result["trigger_count"] = sum(1 for v in result["triggers"].values() if v)
        result["is_learning_event"] = result["trigger_count"] >= PATTERN_THRESHOLD

    result["source"] = source
    result["context"] = context
    return result


def _dir_has_fingerprint(cdir: Path, fp: str) -> bool:
    """Scan one candidate directory for a fingerprint match."""
    for cfile in cdir.glob("*.json"):
        cdata = safe_load_json(cfile)
        if cdata is None or not isinstance(cdata, dict):
            continue
        text = cdata.get("full_content", cdata.get("description", ""))
        if _fingerprint(text) == fp:
            return True
    return False


def _candidate_exists_with_fingerprint(fp: str) -> bool:
    """Flat dedup check across pending+approved candidate dirs."""
    return any(_dir_has_fingerprint(cdir, fp) for cdir in (PENDING_DIR, APPROVED_DIR))


def _maybe_create_candidate(entry: dict, result: dict) -> bool:
    """Create + save skill candidate if warranted; return True if created."""
    if not (result["is_learning_event"] and result["learning_score"] > 0.5):
        return False
    evaluation = evaluate_generalisability(entry["text"], result)
    if not evaluation["is_generalisable"]:
        return False
    if _candidate_exists_with_fingerprint(_fingerprint(entry["text"])):
        entry["skipped_reason"] = "duplicate_candidate"
        return False
    candidate = create_skill_candidate(entry["text"], result, evaluation)
    save_skill_candidate(candidate)
    return True


def _process_one_entry(entry: dict) -> Tuple[bool, bool]:
    """Process one un-ingested entry. Returns (was_processed, candidate_created)."""
    if entry.get("ingested"):
        return False, False
    result = ingest_agent_insight(
        insight_text=entry["text"],
        context=entry.get("context", ""),
        source="persisted_insight",
    )
    entry["ingested"] = True
    entry["ingested_at"] = datetime.now(timezone.utc).isoformat()
    entry["learning_score"] = result["learning_score"]
    return True, _maybe_create_candidate(entry, result)


def _rewrite_insights_files(entries: list) -> None:
    """Persist updated entries to shared + user-scoped insights files."""
    with open(INSIGHTS_FILE, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    user_path = _user_insights_path()
    if user_path != INSIGHTS_FILE:
        try:
            with open(user_path, "w") as uf:
                for entry in entries:
                    uf.write(json.dumps(entry) + "\n")
        except OSError:
            pass


def ingest_pending_insights() -> Dict[str, Any]:
    """Process all un-ingested insights from insights.jsonl.

    Coordinator: load → process each → rewrite. Per-entry logic and
    dedup live in helpers to keep this flat.
    """
    if not INSIGHTS_FILE.exists():
        return {"processed": 0, "candidates": 0, "skipped": 0}
    entries = _load_insights()
    processed = 0
    candidates_created = 0
    skipped = 0
    for entry in entries:
        was_processed, candidate_created = _process_one_entry(entry)
        if was_processed:
            processed += 1
            if candidate_created:
                candidates_created += 1
        else:
            skipped += 1
    _rewrite_insights_files(entries)
    return {
        "processed": processed,
        "candidates": candidates_created,
        "skipped": skipped,
    }


def detect_teaching_moment(message: str, context: List[str]) -> Tuple[bool, Optional[str]]:
    """
    Detect V>> teaching moments in conversation.

    Returns:
        Tuple of (is_teaching_moment, matched_pattern)
    """
    message_lower = message.lower()

    for pattern in TEACHING_PATTERNS:
        if re.search(pattern, message_lower, re.IGNORECASE):
            return True, pattern

    # Check if context shows correction pattern (user corrected assistant)
    if len(context) >= 2:
        prev_msg = context[-2].lower() if len(context) > 1 else ""
        if "wrong" in message_lower or "incorrect" in message_lower:
            if prev_msg:  # There was a previous message that was wrong
                return True, "correction_detected"

    return False, None


def detect_new_term(message: str) -> Tuple[bool, Optional[str]]:
    """
    Detect when user coins or introduces a new term/concept.

    Returns:
        Tuple of (is_new_term, extracted_term)
    """
    for pattern in NAMING_PATTERNS:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            # Try to extract the term being named
            return True, match.group(0)

    return False, None


def detect_generalisation(message: str) -> Tuple[bool, Optional[str]]:
    """
    Detect when a specific solution becomes a general principle.

    Returns:
        Tuple of (is_generalisation, matched_pattern)
    """
    for pattern in GENERALISATION_PATTERNS:
        if re.search(pattern, message, re.IGNORECASE):
            return True, pattern

    return False, None


def _content_based_score(message: str) -> float:
    """Fallback scorer for messages that don't match keyword patterns.

    Uses structural signals to detect actionable insights when keyword
    heuristics return 0. Catches domain-specific insights like
    "dataclass default field gap" that have technical substance but
    don't contain teaching keywords.

    Returns:
        Float between 0-0.8 (capped — keyword matches needed for 1.0)
    """
    score = 0.0
    lower = message.lower()

    # Technical reference signals (file paths, code refs, errors)
    if re.search(r'[\w/]+\.\w{1,4}', message):
        score += 0.15
    if re.search(r'`[^`]+`', message):
        score += 0.10
    if re.search(r'(?:error|exception|bug|fix|issue|regression)', lower):
        score += 0.10

    # Conditional structure ("when X, do Y" — actionable insight shape)
    # No trailing \b — matches verb conjugations (avoids, avoided, verifies)
    if re.search(r'(?:when|if)\b.*(?:then|do|use|avoid|always|never|verify|check|ensure)', lower):
        score += 0.20

    # Actionability words
    action_words = ['always', 'never', 'avoid', 'prefer', 'use', "don't", 'must', 'should']
    action_count = sum(1 for w in action_words if w in lower)
    score += min(action_count * 0.05, 0.15)

    # Length signal (too short = not insight, too long = unfocused)
    words = len(message.split())
    if 20 <= words <= 200:
        score += 0.10

    # Domain-specific terminology
    domain_terms = ['pattern', 'principle', 'heuristic', 'convention', 'idiom',
                    'anti-pattern', 'best practice', 'gotcha', 'pitfall']
    if any(t in lower for t in domain_terms):
        score += 0.15

    return min(score, 0.8)


def calculate_learning_score(
    novelty_score: float,
    is_teaching: bool,
    is_new_term: bool,
    is_generalisation: bool
) -> float:
    """
    Calculate composite learning score from multiple signals.

    Returns:
        Float between 0-1, higher = stronger learning signal
    """
    score = 0.0

    # Novelty contributes up to 0.4
    score += min(novelty_score, 0.4)

    # Teaching moment adds 0.3
    if is_teaching:
        score += 0.3

    # New term adds 0.2
    if is_new_term:
        score += 0.2

    # Generalisation adds 0.1
    if is_generalisation:
        score += 0.1

    return min(score, 1.0)


def _apply_content_fallback(
    message: str, is_learning_event: bool, learning_score: float, trigger_count: int,
) -> Tuple[bool, float, int, float]:
    """Issue #1080: structural-signal fallback for keyword-evading insights."""
    if is_learning_event:
        return is_learning_event, learning_score, trigger_count, 0.0
    content_score = _content_based_score(message)
    if content_score < 0.4:
        return is_learning_event, learning_score, trigger_count, content_score
    return True, max(learning_score, content_score), max(trigger_count, 2), content_score


def detect_learning_event(
    message: str,
    novelty_score: float,
    context: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Core learning event detection — checks novelty + teaching + naming + generalisation."""
    context = context or []
    if is_evo_telemetry(message):
        # /evo friction telemetry is a measurement, not a learnable skill.
        # Refuse at the source so no evo-ff-ev candidate is ever created
        # (the matching promotion floor lives in insight_promoter).
        return _build_learning_event_result(
            False, 0.0, 0, novelty_score,
            False, None, False, None, False, None, 0.0,
        )
    is_teaching, teaching_pattern = detect_teaching_moment(message, context)
    is_new_term, new_term = detect_new_term(message)
    is_generalisation, generalisation_pattern = detect_generalisation(message)
    learning_score = calculate_learning_score(
        novelty_score, is_teaching, is_new_term, is_generalisation,
    )
    trigger_count = sum([
        novelty_score > NOVELTY_THRESHOLD,
        is_teaching,
        is_new_term,
        is_generalisation,
    ])
    is_learning_event = trigger_count >= PATTERN_THRESHOLD
    is_learning_event, learning_score, trigger_count, content_score = _apply_content_fallback(
        message, is_learning_event, learning_score, trigger_count,
    )
    return _build_learning_event_result(
        is_learning_event, learning_score, trigger_count,
        novelty_score, is_teaching, teaching_pattern,
        is_new_term, new_term, is_generalisation,
        generalisation_pattern, content_score,
    )


def _build_learning_event_result(
    is_learning_event, learning_score, trigger_count,
    novelty_score, is_teaching, teaching_pattern,
    is_new_term, new_term, is_generalisation,
    generalisation_pattern, content_score,
):
    """Compose the detect_learning_event return dict — split out for KISS."""
    return {
        "is_learning_event": is_learning_event,
        "learning_score": round(learning_score, 4),
        "trigger_count": trigger_count,
        "triggers": {
            "high_novelty": novelty_score > NOVELTY_THRESHOLD,
            "teaching_moment": is_teaching,
            "new_term": is_new_term,
            "generalisation": is_generalisation,
            "content_based": content_score >= 0.4,
        },
        "details": {
            "novelty_score": round(novelty_score, 4),
            "teaching_pattern": teaching_pattern,
            "new_term": new_term,
            "generalisation_pattern": generalisation_pattern,
            "content_score": round(content_score, 4),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


PROJECT_INDICATORS = (
    "this project", "this codebase", "in this repo",
    "specific to", "only for", "this file", "this function",
)

INFRA_INDICATORS = (
    "claude.md", "rules/", "skills/", "hooks/",
    "learning_detector", "grip", "session", "infrastructure",
)


def _classify_action(is_project_specific: bool, is_infra: bool) -> Tuple[str, str]:
    if is_project_specific:
        return "document_project", "Learning appears specific to current project"
    if is_infra:
        return "update_infrastructure", "Learning relates to GRIP/Claude-Optim infrastructure"
    return "generate_skill", "Learning appears generalisable across projects"


def _extract_skill_name(learning_event: Dict[str, Any]) -> Optional[str]:
    if not (learning_event["triggers"]["new_term"] and learning_event["details"]["new_term"]):
        return None
    term = learning_event["details"]["new_term"]
    name = re.sub(r'[^a-z0-9-]', '-', term.lower().strip())
    return re.sub(r'-+', '-', name).strip('-')


def evaluate_generalisability(
    message: str,
    learning_event: Dict[str, Any],
    project_path: Optional[str] = None
) -> Dict[str, Any]:
    """Evaluate if detected learning is generalisable."""
    message_lower = message.lower()
    is_project_specific = any(ind in message_lower for ind in PROJECT_INDICATORS)
    is_infra_improvement = any(ind in message_lower for ind in INFRA_INDICATORS)
    action, reason = _classify_action(is_project_specific, is_infra_improvement)
    return {
        "is_generalisable": not is_project_specific,
        "action": action,
        "reason": reason,
        "is_project_specific": is_project_specific,
        "is_infra_improvement": is_infra_improvement,
        "suggested_skill_name": _extract_skill_name(learning_event),
        "project_path": project_path,
    }


def create_skill_candidate(
    message: str,
    learning_event: Dict[str, Any],
    evaluation: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Create a skill candidate from detected learning.

    Returns:
        Dict representing the pending skill candidate
    """
    # Generate unique ID
    timestamp = datetime.now(timezone.utc)
    candidate_id = f"skill-{timestamp.strftime('%Y%m%d%H%M%S')}"

    # Determine skill name
    skill_name = evaluation.get("suggested_skill_name")
    if not skill_name:
        # Generate from message (first meaningful words)
        words = re.findall(r'\b[a-z]+\b', message.lower())[:3]
        skill_name = '-'.join(words) if words else f"learned-{candidate_id}"

    candidate = {
        "id": candidate_id,
        "skill_name": skill_name,
        "description": message[:200],
        "full_content": message,
        "learning_score": learning_event["learning_score"],
        "triggers": learning_event["triggers"],
        "evaluation": evaluation,
        "status": "pending",
        "created_at": timestamp.isoformat(),
        "source": {
            "type": "dialectical_learning",
            "project": evaluation.get("project_path"),
        }
    }

    return candidate


# REVERSIBLE (dojo-v2, 2026-10-01): #daily-chat is now tip-of-the-day, so the
# "[GRIP Learning] New skill candidate" broadcast no longer posts there. Set
# back to True to re-enable. The candidate-save machinery below is untouched.
SLACK_BROADCAST_ENABLED = False


def _broadcast_learning_to_slack(candidate: Dict[str, Any]) -> bool:
    """Broadcast a new learning event to #daily-chat (Gen 432).

    Uses macOS Keychain for Slack bot token. Non-blocking — failures are silent.
    """
    if not SLACK_BROADCAST_ENABLED:
        return False
    import subprocess
    try:
        token = subprocess.check_output(
            ["security", "find-generic-password", "-s", "grip-slack-bot", "-w"],
            stderr=subprocess.DEVNULL, timeout=5
        ).decode().strip()
    except Exception:
        return False

    skill_name = candidate.get("skill_name", "unknown")
    score = candidate.get("learning_score", 0)
    desc = candidate.get("description", "")[:120]
    triggers = ", ".join(candidate.get("triggers", [])[:3])

    text = (
        f"[GRIP Learning] New skill candidate: *{skill_name}*\n"
        f"Score: {score:.2f} | Triggers: {triggers}\n"
        f"{desc}"
    )

    channel = "C0EXAMPLE01"  # #daily-chat
    payload = json.dumps({"channel": channel, "text": text})

    try:
        subprocess.run(
            ["curl", "-s", "-X", "POST", "https://slack.com/api/chat.postMessage",
             "-H", f"Authorization: Bearer {token}",
             "-H", "Content-Type: application/json",
             "-d", payload],
            capture_output=True, timeout=10
        )
        return True
    except Exception:
        return False


def _candidate_id_taken(candidate_id: str) -> bool:
    """True when any candidate directory already holds this id."""
    return any(
        (cdir / f"{candidate_id}.json").exists()
        for cdir in (PENDING_DIR, APPROVED_DIR, REJECTED_DIR)
    )


def _free_candidate_id(base: str) -> str:
    """The base id, or the first base-N suffix that no candidate file uses yet.

    Ids are minted at one-second resolution and a batch ingest creates several
    candidates in one second. Saving them under one id kept only the last file
    while the ingest still counted every one (2026-10-01: 7 reported, 1 on
    disk). One process saving a batch, the observed case, now keeps them all;
    two processes saving in the same instant can still race.
    """
    candidate_id, n = base, 1
    while _candidate_id_taken(candidate_id):
        n += 1
        candidate_id = f"{base}-{n}"
    return candidate_id


def save_skill_candidate(candidate: Dict[str, Any]) -> Path:
    """
    Save skill candidate to pending directory.
    Broadcasts to team Slack on success (Gen 432).

    The id gains a numeric suffix when another candidate already uses it, so
    the file name and candidate["id"] always agree and nothing is overwritten.

    Returns:
        Path to saved candidate file
    """
    ensure_directories()

    candidate["id"] = _free_candidate_id(candidate["id"])
    candidate_file = PENDING_DIR / f"{candidate['id']}.json"
    with open(candidate_file, 'w') as f:
        json.dump(candidate, f, indent=2)

    # Broadcast to team (non-blocking)
    try:
        _broadcast_learning_to_slack(candidate)
    except Exception:
        pass

    return candidate_file


def get_pending_candidates() -> List[Dict[str, Any]]:
    """Get all pending skill candidates."""
    ensure_directories()

    candidates = []
    for candidate_file in PENDING_DIR.glob("*.json"):
        data = safe_load_json(candidate_file)
        if data is not None and isinstance(data, dict):
            candidates.append(data)

    return sorted(candidates, key=lambda x: x.get("created_at", ""), reverse=True)


def approve_candidate(candidate_id: str) -> Optional[Path]:
    """
    Move candidate from pending to approved.

    Returns:
        Path to approved candidate file, or None if not found
    """
    pending_file = PENDING_DIR / f"{candidate_id}.json"
    candidate = safe_load_json(pending_file)
    if candidate is None:
        return None

    candidate["status"] = "approved"
    candidate["approved_at"] = datetime.now(timezone.utc).isoformat()

    approved_file = APPROVED_DIR / f"{candidate_id}.json"
    with open(approved_file, 'w') as f:
        json.dump(candidate, f, indent=2)

    pending_file.unlink()
    return approved_file


def reject_candidate(candidate_id: str, reason: str = "") -> Optional[Path]:
    """
    Move candidate from pending to rejected.

    Returns:
        Path to rejected candidate file, or None if not found
    """
    pending_file = PENDING_DIR / f"{candidate_id}.json"
    candidate = safe_load_json(pending_file)
    if candidate is None:
        return None

    candidate["status"] = "rejected"
    candidate["rejected_at"] = datetime.now(timezone.utc).isoformat()
    candidate["rejection_reason"] = reason

    rejected_file = REJECTED_DIR / f"{candidate_id}.json"
    with open(rejected_file, 'w') as f:
        json.dump(candidate, f, indent=2)

    pending_file.unlink()
    return rejected_file


def format_notification(candidate: Dict[str, Any]) -> str:
    """
    Format notification for V>> approval via AskUserQuestion.

    Returns:
        Formatted notification string with candidate ID for approval flow
    """
    return f"""[GRIP LEARNING] id={candidate['id']} skill={candidate['skill_name']}
Score: {candidate['learning_score']:.2f} | Triggers: {', '.join(k for k, v in candidate['triggers'].items() if v)}
{candidate['description'][:100]}..."""


def log_learning_event(event: Dict[str, Any]) -> None:
    """Log learning event to metrics file."""
    metric_entry = {
        "event": "learning_detected",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "learning_score": event.get("learning_score", 0),
        "trigger_count": event.get("trigger_count", 0),
        "triggers": event.get("triggers", {}),
        "is_learning_event": event.get("is_learning_event", False)
    }

    with open(METRICS_FILE, 'a') as f:
        f.write(json.dumps(metric_entry) + '\n')


_DEFAULT_COHERENCE = {
    "coherence_passed": True,
    "coherence_score": 1.0,
    "coherence_method": "not_checked",
}


def _compute_novelty(message: str, novelty_score: float) -> Tuple[float, dict, bool]:
    """Resolve novelty score. Returns (score, coherence_meta, embedding_succeeded).

    W5 follow-up: routes via get_engine() dispatcher so GRIP_EMBED_BACKEND
    toggles transparently. Gen 148: never silently bypass embedding;
    Gen 153: coherence gate may zero out gibberish.
    """
    if novelty_score != 0.0:
        return novelty_score, dict(_DEFAULT_COHERENCE), True
    if not HAS_EMBEDDINGS:
        print("[WARN] Embeddings unavailable - novelty scoring disabled", file=sys.stderr)
        return 0.0, dict(_DEFAULT_COHERENCE), False
    try:
        engine = get_engine()
        engine.init_schema()
        score, coherence = engine._calculate_novelty(message)
        engine.close()
        return score, coherence, True
    except Exception as exc:
        print(f"[ERROR] Embedding failed: {exc} - novelty scoring disabled", file=sys.stderr)
        return 0.0, dict(_DEFAULT_COHERENCE), False


def _action_from_evaluation(evaluation: dict) -> Optional[str]:
    """Map evaluation.action -> result.action_taken; None means proceed to candidate."""
    return {
        "document_project": "document_project",
        "update_infrastructure": "flag_for_infrastructure",
    }.get(evaluation["action"])


def process_message(
    message: str,
    novelty_score: float = 0.0,
    context: Optional[List[str]] = None,
    project_path: Optional[str] = None,
    auto_notify: bool = True
) -> Dict[str, Any]:
    """Main entry point: process a message through the learning detection pipeline."""
    novelty_score, coherence_meta, embedding_succeeded = _compute_novelty(message, novelty_score)
    learning_event = detect_learning_event(message, novelty_score, context)
    result = {
        "learning_event": learning_event,
        "candidate": None,
        "notification": None,
        "action_taken": None,
        "embedding_succeeded": embedding_succeeded,
        "coherence": coherence_meta,
    }
    log_learning_event(learning_event)
    if not learning_event["is_learning_event"]:
        result["action_taken"] = "no_learning_detected"
        return result
    evaluation = evaluate_generalisability(message, learning_event, project_path)
    result["evaluation"] = evaluation
    early_action = _action_from_evaluation(evaluation)
    if early_action is not None:
        result["action_taken"] = early_action
        return result
    candidate = create_skill_candidate(message, learning_event, evaluation)
    candidate_path = save_skill_candidate(candidate)
    result["candidate"] = candidate
    result["candidate_path"] = str(candidate_path)
    result["action_taken"] = "skill_candidate_created"
    if auto_notify:
        result["notification"] = format_notification(candidate)
    return result


def _build_arg_parser():
    """Compose the CLI parser. Extracted from main for KISS."""
    import argparse
    parser = argparse.ArgumentParser(
        description="Autonomous Learning Engine - Dialectical Learning Detector",
    )
    sub = parser.add_subparsers(dest="command", help="Commands")
    p_process = sub.add_parser("process", help="Process message for learning")
    p_process.add_argument("message", help="Message to process")
    p_process.add_argument("--novelty", type=float, default=0.0, help="Pre-calculated novelty score")
    p_process.add_argument("--project", help="Project path")
    p_process.add_argument("--no-notify", action="store_true", help="Suppress notification")
    sub.add_parser("list", help="List pending skill candidates")
    p_approve = sub.add_parser("approve", help="Approve skill candidate")
    p_approve.add_argument("candidate_id", help="Candidate ID to approve")
    p_reject = sub.add_parser("reject", help="Reject skill candidate")
    p_reject.add_argument("candidate_id", help="Candidate ID to reject")
    p_reject.add_argument("--reason", default="", help="Rejection reason")
    p_detect = sub.add_parser("detect", help="Quick learning detection")
    p_detect.add_argument("message", help="Message to check")
    p_detect.add_argument("--novelty", type=float, default=0.0, help="Novelty score")
    p_ingest = sub.add_parser("ingest", help="Ingest pending agent insights")
    p_ingest.add_argument("--text", help="Persist and ingest a single insight inline")
    p_ingest.add_argument("--context", default="", help="Context for inline insight")
    sub.add_parser("consolidate", help="Merge 3+ similar insights")
    sub.add_parser("generate-rules", help="Generate rules from approved insights")
    sub.add_parser("scan", help="Scan insights for patterns and candidates")
    sub.add_parser("init", help="Initialise learning directories")
    return parser


def _cmd_scan(args):  # noqa: ARG001
    print(json.dumps({
        "status": "ok",
        "consolidated": consolidate_insights(),
        "ingested": ingest_pending_insights(),
        "pending_candidates": len(get_pending_candidates()),
    }, indent=2))


def _cmd_consolidate(args):  # noqa: ARG001
    print(json.dumps({"status": "ok", **consolidate_insights()}, indent=2))


def _cmd_generate_rules(args):  # noqa: ARG001
    print(json.dumps({"status": "ok", **generate_learned_rules()}, indent=2))


def _cmd_ingest(args):
    if args.text:
        insight_id = persist_insight(args.text, context=args.context)
        result = ingest_agent_insight(args.text, context=args.context)
        print(json.dumps({
            "status": "ok",
            "insight_id": insight_id,
            "learning_event": result,
        }, indent=2))
    else:
        print(json.dumps({"status": "ok", **ingest_pending_insights()}, indent=2))


def _cmd_init(args):  # noqa: ARG001
    ensure_directories()
    print(json.dumps({
        "status": "ok",
        "directories": {
            "learning": str(LEARNING_DIR),
            "pending": str(PENDING_DIR),
            "approved": str(APPROVED_DIR),
            "rejected": str(REJECTED_DIR),
        },
    }, indent=2))


def _cmd_process(args):
    result = process_message(
        message=args.message,
        novelty_score=args.novelty,
        project_path=args.project,
        auto_notify=not args.no_notify,
    )
    print(json.dumps(result, indent=2, default=str))
    if result.get("notification"):
        print(result["notification"], file=sys.stderr)


def _cmd_detect(args):
    print(json.dumps(detect_learning_event(args.message, args.novelty), indent=2))


def _cmd_list(args):  # noqa: ARG001
    candidates = get_pending_candidates()
    print(json.dumps({
        "status": "ok",
        "count": len(candidates),
        "candidates": candidates,
    }, indent=2))


def _print_path_or_error(path, action: str, candidate_id: str) -> None:
    if path:
        print(json.dumps({"status": "ok", "action": action, "path": str(path)}))
    else:
        print(json.dumps({
            "status": "error",
            "message": f"Candidate not found: {candidate_id}",
        }))


def _cmd_approve(args):
    _print_path_or_error(approve_candidate(args.candidate_id), "approved", args.candidate_id)


def _cmd_reject(args):
    _print_path_or_error(
        reject_candidate(args.candidate_id, args.reason),
        "rejected",
        args.candidate_id,
    )


COMMANDS = {
    "scan": _cmd_scan,
    "consolidate": _cmd_consolidate,
    "generate-rules": _cmd_generate_rules,
    "ingest": _cmd_ingest,
    "init": _cmd_init,
    "process": _cmd_process,
    "detect": _cmd_detect,
    "list": _cmd_list,
    "approve": _cmd_approve,
    "reject": _cmd_reject,
}


def main():
    """CLI interface for learning detector — dispatch table over subcommands."""
    parser = _build_arg_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return
    handler = COMMANDS.get(args.command)
    if handler is not None:
        handler(args)


if __name__ == "__main__":
    main()
