"""Recall First (Rule 0) — shared retrieval logic behind the /recall gates.

Extracted from grip-first-retrieval-hook.py and grip-first-web-hook.py
to eliminate 7 duplicated elements (DRY, YSH).

Used by both PreToolUse hooks for type-discriminated semantic matching
against KONO's embed_type taxonomy.
"""

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError

# --- Constants ---

SIMILARITY_FLOOR = 0.12
HIGH_SIMILARITY_FLOOR = 0.25
KONO_TIMEOUT = 1.5  # seconds — prevents consuming the full 5s hook budget

# High-confidence GRIP types — always about GRIP internals
GRIP_TYPES_HIGH = frozenset({
    'concept_mode', 'concept_command', 'concept_agent', 'concept_intent',
    'concept_feedback', 'insight', 'learning', 'permanent', 'procedural', 'ltm',
})

# Medium-confidence GRIP types — can be about external tech
# Require higher similarity to trigger
GRIP_TYPES_MEDIUM = frozenset({
    'concept_skill', 'concept_workflow', 'concept_solution', 'emergent',
})

# Fallback keywords — only used when KONO is unavailable
GRIP_KEYWORDS = re.compile(
    r'\b(mode|skill|gate|genome|kono|converge|broly|rsi|cube|wave|'
    r'oracle|recall|grip|principle|rule|insight|gene)\b',
    re.IGNORECASE
)
GRIP_PATHS = re.compile(
    r'(modes/|skills/|rules/|lib/|agents/|docs/|commands/|\.claude/)',
    re.IGNORECASE
)


# --- Functions ---

def evaluate_results(results):
    """Score KONO results against GRIP type taxonomy.

    Returns (is_grip, score, embed_type, snippet).
    """
    for r in results:
        score = r.get("similarity", 0)
        if score < SIMILARITY_FLOOR:
            continue
        embed_type = r.get("type", "")

        if embed_type in GRIP_TYPES_HIGH:
            content = r.get("content", "")[:80]
            return True, score, embed_type, content

        if embed_type in GRIP_TYPES_MEDIUM and score >= HIGH_SIMILARITY_FLOOR:
            content = r.get("content", "")[:80]
            return True, score, embed_type, content

    return False, 0.0, "", ""


def keyword_check(prompt):
    """Fast fallback when KONO is unavailable."""
    grip_matches = GRIP_KEYWORDS.findall(prompt)
    path_matches = GRIP_PATHS.findall(prompt)
    return len(grip_matches) >= 2 or bool(path_matches), grip_matches[:3]


def _is_commander_session() -> bool:
    """Detect commander/MCP sessions to skip embedding calls (SIGSEGV guard)."""
    if os.environ.get("GRIP_GUILD_BOT") == "1":
        return True
    if os.environ.get("CLAUDE_NON_INTERACTIVE"):
        return True
    return False


def kono_search(prompt, timeout=KONO_TIMEOUT):
    """Run KONO semantic search with timeout guard.

    Returns (is_grip, score, embed_type, snippet).
    - is_grip=True: KONO has a GRIP-typed match
    - is_grip=False: KONO searched, no GRIP match
    - is_grip=None: KONO unavailable or timed out
    """
    # Skip embeddings in commander sessions — avoids lembed SIGSEGV from
    # concurrent model loading (5 crashes in 2 min, Gen 499)
    if _is_commander_session():
        return None, 0.0, "", ""
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_raw_kono_search, prompt)
            results = future.result(timeout=timeout)
        return evaluate_results(results)
    except TimeoutError:
        return None, 0.0, "", ""
    except Exception:
        return None, 0.0, "", ""


def _raw_kono_search(prompt):
    """Direct KONO query — called inside timeout guard."""
    import sys as _sys
    claude_dir = os.environ.get("HOME", "") + "/.claude"
    _sys.path.insert(0, os.path.join(claude_dir, "lib"))
    from embeddings import get_engine  # W5(a): dispatch via get_engine()
    e = get_engine()
    results = e.search_similar(prompt[:500], limit=5)
    e.close()
    return results


# --- Query Refinement (Convergent Search) ---

FILLER_WORDS = frozenset({
    'the', 'a', 'an', 'is', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
    'could', 'should', 'may', 'might', 'shall', 'can',
    'i', 'me', 'my', 'we', 'our', 'you', 'your',
    'find', 'get', 'show', 'tell', 'give', 'look', 'search',
    'where', 'what', 'how', 'when', 'which', 'who',
    'about', 'from', 'with', 'that', 'this', 'for', 'into',
    'of', 'in', 'on', 'at', 'to', 'by', 'up', 'out',
    'just', 'also', 'very', 'really', 'quite', 'some', 'any',
    'please', 'want', 'need', 'try', 'recall', 'remember',
})

GRIP_SYNONYMS = {
    'mode': ['reasoning mode'],
    'skill': ['capability', 'SKILL.md'],
    'gate': ['safety gate', 'enforcement'],
    'converge': ['convergence loop'],
    'genome': ['gene fitness', 'evolution'],
    'kono': ['memory substrate', 'semantic memory'],
    'rsi': ['recursive self-improvement'],
    'broly': ['meta-agent', 'mesh council'],
    'article': ['post', 'publication', 'linkedin'],
    'url': ['link', 'reference'],
    'pattern': ['insight', 'learning'],
}


def refine_query(original, attempt, prior_scores=None):
    """Generate a query variation for convergent KONO search.

    Strategy by attempt: 0=original, 1=strip filler, 2=entity extraction+synonyms.
    """
    if attempt == 0:
        return original
    if attempt == 1:
        return _strip_filler(original)
    if attempt == 2:
        return _extract_entities_and_expand(original)
    return original


def _strip_filler(query):
    """Remove filler words and quotes, preserving content words."""
    cleaned = query.replace('"', '').replace("'", '')
    words = cleaned.split()
    kept = [w for w in words if w.lower() not in FILLER_WORDS and len(w) > 1]
    return ' '.join(kept) if kept else query


def _extract_entities_and_expand(query):
    """Extract likely entities and expand with GRIP synonyms."""
    cleaned = query.replace('"', '').replace("'", '')
    words = cleaned.split()
    entities = []
    expansions = []
    for w in words:
        w_lower = w.lower()
        if w_lower in FILLER_WORDS:
            continue
        if w[0].isupper() or '-' in w or '_' in w or len(w) > 5 or w_lower in GRIP_SYNONYMS:
            entities.append(w)
            if w_lower in GRIP_SYNONYMS:
                expansions.append(GRIP_SYNONYMS[w_lower][0])
    result = ' '.join(entities + expansions)
    return result if result else query


def convergent_kono_search(query, max_attempts=3, timeout=KONO_TIMEOUT):
    """Convergent KONO search with query refinement.

    Applies converge kernel: try up to max_attempts query variations.
    Returns (is_grip, score, embed_type, snippet, query_trail).
    """
    query_trail = []
    best = (False, 0.0, '', '')
    prior_scores = []

    for attempt in range(max_attempts):
        refined = refine_query(query, attempt, prior_scores)

        if any(t['query'] == refined for t in query_trail):
            query_trail.append({
                'attempt': attempt, 'query': refined,
                'score': 0.0, 'skipped': 'duplicate',
            })
            continue

        is_grip, score, etype, snippet = kono_search(refined, timeout=timeout)
        query_trail.append({
            'attempt': attempt, 'query': refined, 'score': round(score, 4),
        })
        prior_scores.append(score)

        if is_grip is True:
            return (True, score, etype, snippet, query_trail)
        if is_grip is None:
            return (None, 0.0, '', '', query_trail)
        if score > best[1]:
            best = (False, score, etype, snippet)

    return (*best, query_trail)


def record_embodiment(was_grip_hit):
    """Record GRIP-FIRST embodiment observation."""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(os.environ.get("HOME", ""), ".claude"))
        from lib.embodiment_tracker import EmbodimentTracker
        tracker = EmbodimentTracker()
        session_id = os.environ.get("CLAUDE_SESSION_ID", f"hook-{int(time.time())}")
        tracker.record_session(
            principle="GRIP-FIRST",
            session_id=session_id,
            violation_count=0 if was_grip_hit else 1,
            convergence_depth=4 if was_grip_hit else 1,
        )
        tracker.save()
    except Exception:
        pass  # Never fail the hook for embodiment tracking


def allow_response():
    """Print the schema-valid silent allow (an empty JSON object) to stdout.

    Historical note: this used to print {"decision": "allow"} — vocabulary the
    harness rejects (legacy `decision` accepts only approve|block), so every
    such output failed hook validation. See lib/hook_response.py.
    """
    from hook_response import emit, silent_allow
    emit(silent_allow())
