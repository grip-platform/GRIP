"""Backlog processing pipeline — stages INGEST through PROPOSE.

Stages 1-2 (INGEST, EXTRACT) are deterministic — no LLM calls.
Stages 3-4 (REASON, PROPOSE) use Claude API with critical reasoning gates.
"""

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

from lib.backlog_item import (
    BacklogItem, BACKLOG_DIR, save_item, load_item, move_item, STAGE_DIRS,
    now_iso
)
from lib.backlog_gates import (
    anti_sycophancy_gate, cost_gate, coherence_gate, should_invoke_mesh
)
from lib.backlog_manager import contains_credential


# --- Stage 1: INGEST ---

def stage_ingest(item: BacklogItem) -> BacklogItem:
    """Normalise raw input into the item schema. No reasoning."""
    if item.source_url and not item.content_body:
        item = _ingest_url(item)
    elif item.source_file_path:
        item = _ingest_file(item)

    # Detect content type if not explicitly set
    if item.content_type == "note" and item.source_url:
        item.content_type = "url"
    elif item.source_file_path:
        item.content_type = _detect_file_type(item.source_file_path)

    return item


def _ingest_url(item: BacklogItem) -> BacklogItem:
    """Fetch URL content and store as markdown in content_body."""
    url = item.source_url
    try:
        # Use curl for reliability — no Python dependency needed
        result = subprocess.run(
            ["curl", "-sL", "--max-time", "30", "-H",
             "User-Agent: GRIP-Backlog/1.0", url],
            capture_output=True, text=True, timeout=35
        )
        if result.returncode == 0 and result.stdout:
            # Strip HTML tags for basic text extraction
            text = _strip_html(result.stdout)
            # Truncate to 5000 chars to avoid embedding oversized content
            item.content_body = text[:5000]
        else:
            item.error = f"URL fetch failed: HTTP error or timeout"
    except (subprocess.TimeoutExpired, OSError) as e:
        item.error = f"URL fetch failed: {e}"
    return item


def _strip_html(html: str) -> str:
    """Basic HTML to text conversion for content extraction only.

    Not a security boundary — output is stored for LLM processing, not rendered as HTML.
    """
    # codeql[py/bad-tag-filter] Text extraction only, not a security boundary
    # Remove script and style blocks
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Remove tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    # Decode common entities
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    return text


def _ingest_file(item: BacklogItem) -> BacklogItem:
    """Read file content based on type."""
    path = Path(item.source_file_path)
    if not path.exists():
        item.error = f"File not found: {path}"
        return item

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        item = _ingest_pdf(item, path)
    elif suffix in (".md", ".txt", ".json", ".py", ".js", ".ts", ".yaml", ".yml"):
        item.content_body = path.read_text(errors="replace")[:5000]
    elif suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        item.content_type = "image"
        item.content_body = f"[Image: {path.name}]"
        item.content_attachments = [str(path)]
    else:
        item.content_body = f"[Unsupported file type: {suffix}]"
        item.error = f"Unsupported file type: {suffix}"
    if contains_credential(item.content_body):
        # stage_reason sends this text to a model that can fail over to an
        # outside provider, so a file holding a secret is never read into an
        # item; the file itself stays on this machine (Codex on GRIP#9809).
        item.content_body = (f"[Redacted: {path.name} looks like it holds a password, key or "
                             "token. It stays on this machine and is not sent to any model.]")
    return item


def _ingest_pdf(item: BacklogItem, path: Path) -> BacklogItem:
    """Extract text from PDF using pdf_batch_processor if available."""
    try:
        # Try GRIP's pdf_batch_processor
        from lib.pdf_batch_processor import extract_text_from_pdf
        item.content_body = extract_text_from_pdf(str(path))[:5000]
        item.content_type = "document"
    except ImportError:
        # Fallback: use pdftotext if available
        try:
            result = subprocess.run(
                ["pdftotext", str(path), "-"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                item.content_body = result.stdout[:5000]
                item.content_type = "document"
            else:
                item.content_body = f"[PDF: {path.name}]"
                item.error = "pdftotext failed"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            item.content_body = f"[PDF: {path.name} — extraction unavailable]"
            item.error = "No PDF extractor available"
    return item


def _detect_file_type(file_path: str) -> str:
    """Detect content type from file extension."""
    suffix = Path(file_path).suffix.lower()
    type_map = {
        ".pdf": "document", ".docx": "document", ".doc": "document",
        ".md": "note", ".txt": "note",
        ".png": "image", ".jpg": "image", ".jpeg": "image",
        ".gif": "image", ".webp": "image",
        ".py": "code", ".js": "code", ".ts": "code",
        ".json": "code", ".yaml": "code", ".yml": "code",
    }
    return type_map.get(suffix, "note")


# --- Stage 2: EXTRACT ---

def stage_extract(item: BacklogItem) -> BacklogItem:
    """Structured extraction from normalised content. Mechanical, not inferential."""
    body = item.content_body

    # Title: first meaningful sentence or truncated body
    if not item.content_title:
        item.content_title = _extract_title(body)

    # Tags: keyword extraction from controlled vocabulary + freeform
    item.tags = _extract_tags(body)

    # Explicit action detection
    item.actions = _detect_explicit_actions(body)

    return item


def _extract_title(body: str) -> str:
    """Extract title from content body."""
    if not body:
        return "Untitled"

    # First line if it looks like a title (short, no period at end)
    first_line = body.split("\n")[0].strip()
    if first_line and len(first_line) < 100 and not first_line.endswith("."):
        return first_line

    # First sentence
    match = re.match(r'^(.+?[.!?])\s', body)
    if match and len(match.group(1)) < 120:
        return match.group(1)

    # Truncated body
    return body[:80] + ("..." if len(body) > 80 else "")


def _extract_tags(body: str) -> list[str]:
    """Extract tags from body text using keyword detection."""
    tags = []
    body_lower = body.lower()

    # Technology tags
    tech_keywords = {
        "python": "python", "javascript": "javascript", "typescript": "typescript",
        "react": "react", "nextjs": "nextjs", "next.js": "nextjs",
        "docker": "docker", "kubernetes": "kubernetes", "k8s": "kubernetes",
        "redis": "redis", "postgres": "postgres", "postgresql": "postgres",
        "github": "github", "git": "git", "ci/cd": "cicd", "ci": "cicd",
        "api": "api", "rest": "api", "graphql": "graphql",
        "aws": "aws", "azure": "azure", "gcp": "gcp",
        "slack": "slack", "jira": "jira",
        "grip": "grip", "rsi": "rsi", "kono": "kono",
    }
    for keyword, tag in tech_keywords.items():
        if keyword in body_lower and tag not in tags:
            tags.append(tag)

    # Priority tags
    if any(m in body_lower for m in ("urgent", "asap", "critical", "blocking")):
        tags.append("urgent")

    # Limit to 5 tags
    return tags[:5]


def _detect_explicit_actions(body: str) -> list[dict]:
    """Detect explicit action verbs in the text."""
    actions = []
    body_lower = body.lower()

    action_patterns = [
        (r'\b(check|verify|confirm)\b', "task", "Verify: "),
        (r'\b(fix|repair|resolve|debug)\b', "task", "Fix: "),
        (r'\b(research|investigate|explore|look into)\b', "research", "Research: "),
        (r'\b(ask|discuss|message|ping|tell)\b', "communication", "Communicate: "),
        (r'\b(schedule|plan|organise|organize)\b', "task", "Plan: "),
        (r'\b(review|audit|assess)\b', "task", "Review: "),
        (r'\b(read|study|learn)\b', "research", "Study: "),
        (r'\b(create|build|implement|write|add)\b', "task", "Create: "),
    ]

    # Pre-split sentences once (O(n) not O(patterns × sentences))
    sentences = [(s.strip(), s.strip().lower()) for s in re.split(r'[.!?\n]', body) if s.strip()]

    for pattern, action_type, prefix in action_patterns:
        if not re.search(pattern, body_lower):
            continue
        match = next((s for s, sl in sentences if re.search(pattern, sl)), None)
        if match:
            actions.append({
                "action": match[:200],
                "type": action_type,
                "priority": "P2",
                "confidence": 0.4,
                "reasoning": f"Explicit action verb detected: {pattern}",
                "requires_review": True,
            })
        if len(actions) >= 3:
            break

    return actions


# --- Stage 3: REASON ---

def stage_reason(item: BacklogItem, config: Optional[dict] = None) -> BacklogItem:
    """AI reasoning stage. Categorise, summarise, connect.

    This stage makes LLM calls — gated by cost_gate.
    """
    if config is None:
        config = _load_config()

    word_count = len(item.content_body.split())

    # Cost gate check
    daily_spent = _get_daily_spend()
    ceiling = config.get("cost", {}).get("daily_ceiling_usd", 0.50)
    gate = cost_gate(daily_spent, ceiling, word_count)
    if not gate["allowed"]:
        item.error = gate["reason"]
        return item

    # Coherence gate
    if not coherence_gate(item.content_body):
        item.error = "Content failed coherence gate"
        return item

    start_ms = int(time.time() * 1000)

    # Use Claude API for reasoning
    try:
        result = _reason_with_llm(item, gate["model"])
        if result:
            item.category = result.get("category", "archive")
            item.summary = result.get("summary", "")
            item.confidence = result.get("confidence", 0.5)
            item.reasoning_trace = result.get("reasoning", "")

            # Merge LLM-suggested tags with extracted tags
            llm_tags = result.get("tags", [])
            item.tags = list(set(item.tags + llm_tags))[:5]
    except Exception as e:
        item.error = f"LLM reasoning failed: {e}"

    item.processing_time_ms = int(time.time() * 1000) - start_ms
    return item


def _reason_with_llm(item: BacklogItem, model: str) -> Optional[dict]:
    """Categorise + summarise a backlog item via GRIP's canonical AI syscall.

    Routed through ``lib.hal_llm_adapter`` (syscall-doctrine), NOT a raw
    ``anthropic.Anthropic()`` SDK construction. This is value-producing
    INTERNAL work (categorisation/summarisation) — NOT a Goodhart anchor or an
    exogenous council — so it is a legitimate reroute, and routing it through
    HAL makes it inherit the provider failover cascade (RAILLM → Groq → Gemini
    → xAI). A transient Anthropic server 429 no longer throws and errors the
    item: HAL transparently fails over to a working provider. ``model`` is
    forwarded so a caller-pinned route is honoured; passing it explicitly also
    bypasses the cheap econ-default so categorisation keeps its intended model.

    Fail-DEGRADED to the deterministic rule-based reasoner on import failure
    (no GRIP lib) OR when the whole HAL chain is exhausted (the neutral
    sentinel / empty text) — so a backlog item is ALWAYS categorised, never
    left in limbo. ``audit=False``: backlog triage is not on the product IDR
    surface.
    """
    try:
        from lib.hal_llm_adapter import HALLLMAdapter
    except ImportError:
        # No GRIP HAL lib on path — deterministic fallback.
        return _rule_based_reason(item)

    prompt = f"""Categorise and summarise this backlog item.

Content type: {item.content_type}
Title: {item.content_title or 'None'}
Body:
{item.content_body[:2000]}

Respond with JSON only:
{{
  "category": one of: task, reference, idea, decision, learning, communication, project, archive
  "summary": 1-2 sentence summary of the item's essence
  "tags": up to 3 additional keyword tags (lowercase, no duplicates with existing)
  "confidence": 0.0-1.0 how confident you are in the categorisation
  "reasoning": brief explanation of why this category
}}

Rules:
- Bias toward 'archive' when uncertain — it's better to under-classify than over-classify
- 'task' requires a clear actionable item
- 'idea' is for creative thoughts not yet actionable
- 'decision' requires a choice between alternatives
- 'learning' is for new knowledge or patterns"""

    # infer(model=…, audit=False): the explicit model is the caller's pinned
    # route (e.g. the cost-gate-selected SKU), forwarded so the cheap
    # econ-default does not silently override the intended model. audit=False is
    # fail-DEGRADED — it still returns text via the legacy provider chain when
    # the RAILLM primary is down, so categorisation survives a HAL-server outage
    # too. (generate() takes no model kwarg; infer() is the model-aware entry.)
    result = HALLLMAdapter().infer(prompt, model=model, audit=False)
    parsed = _parse_llm_json((result.text or "").strip())
    if parsed is None or "category" not in parsed:
        # HAL chain exhausted (neutral sentinel / empty) or off-schema reply →
        # deterministic fallback so the item is still categorised.
        return _rule_based_reason(item)
    return parsed


def _parse_llm_json(text: str) -> Optional[dict]:
    """Extract the JSON object from an LLM reply. None when unparseable.

    Spans the FIRST '{' to the LAST '}' (the schema is a single flat object,
    possibly wrapped in prose/fences). Tolerant of trailing text; returns None
    on malformed JSON so the caller falls back deterministically."""
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end])
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _rule_based_reason(item: BacklogItem) -> dict:
    """Fallback categorisation when Claude API is unavailable."""
    body = item.content_body.lower()
    category = "archive"
    confidence = 0.3

    if any(w in body for w in ("fix", "bug", "error", "broken", "failing")):
        category = "task"
        confidence = 0.6
    elif any(w in body for w in ("check", "verify", "update", "create", "implement",
                                 "add", "build", "migrate", "deploy", "setup",
                                 "configure", "install", "remove", "delete",
                                 "refactor", "upgrade", "downgrade")):
        category = "task"
        confidence = 0.5
    elif any(w in body for w in ("what if", "idea:", "maybe", "could we")):
        category = "idea"
        confidence = 0.5
    elif any(w in body for w in ("decide", "choose", "should we", "vs", "versus")):
        category = "decision"
        confidence = 0.5
    elif any(w in body for w in ("til:", "learned", "turns out", "discovered")):
        category = "learning"
        confidence = 0.5
    elif any(w in body for w in ("ask", "tell", "message", "email", "call")):
        category = "communication"
        confidence = 0.5
    elif item.content_type == "url":
        category = "reference"
        confidence = 0.4

    return {
        "category": category,
        "summary": item.content_title or item.content_body[:100],
        "tags": [],
        "confidence": confidence,
        "reasoning": f"Rule-based: matched category '{category}' from keywords",
    }


# --- Stage 4: PROPOSE ---

def stage_propose(item: BacklogItem, config: Optional[dict] = None) -> BacklogItem:
    """Generate concrete next-step proposals. Every proposal needs confidence + reasoning."""
    if config is None:
        config = _load_config()

    # Merge mechanical actions (from EXTRACT) with category-based proposals
    proposals = list(item.actions)

    # Add category-specific proposals
    category_proposals = _propose_by_category(item)
    proposals.extend(category_proposals)

    # Apply anti-sycophancy gate
    reasoning_config = config.get("reasoning", {})
    proposals = anti_sycophancy_gate(
        item.content_body,
        proposals,
        confidence_floor=reasoning_config.get("confidence_floor", 0.5),
        max_actions=reasoning_config.get("max_actions_per_item", 3),
    )

    item.actions = proposals
    return item


_CATEGORY_TEMPLATES: dict[str, tuple[str, str, str, float]] = {
    # category: (action_prefix, type, priority, confidence)
    "task":          ("Create GitHub issue", "task", "P2", 0.6),
    "reference":     ("Embed in KONO", "archive", "P3", 0.7),
    "idea":          ("Incubate", "research", "P3", 0.5),
    "decision":      ("Create decision framework", "decision", "P2", 0.6),
    "learning":      ("Skill candidate", "skill_candidate", "P3", 0.5),
    "communication": ("Draft message", "communication", "P2", 0.5),
}


def _propose_by_category(item: BacklogItem) -> list[dict]:
    """Generate proposals based on item category via dispatch table."""
    category = item.category or "archive"
    template = _CATEGORY_TEMPLATES.get(category)
    if not template:
        return []
    action_prefix, action_type, priority, confidence = template
    return [{
        "action": f"{action_prefix}: {item.content_title}",
        "type": action_type,
        "priority": priority,
        "confidence": confidence,
        "reasoning": f"Category '{category}' — routed via dispatch table",
        "requires_review": True,
    }]


# --- Helpers ---

def _load_config() -> dict:
    """Load backlog config.json."""
    config_path = BACKLOG_DIR / "config.json"
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


def _get_daily_spend() -> float:
    """Get today's API spend from scanner log."""
    log_path = BACKLOG_DIR / "scanner.log"
    if not log_path.exists():
        return 0.0

    today = time.strftime("%Y-%m-%d")
    total = 0.0
    try:
        with open(log_path) as f:
            for line in f:
                if today in line and "cost=" in line:
                    match = re.search(r'cost=(\d+\.?\d*)', line)
                    if match:
                        total += float(match.group(1))
    except OSError:
        pass
    return total


def process_item(item: BacklogItem, config: Optional[dict] = None) -> BacklogItem:
    """Run full pipeline: INGEST -> EXTRACT -> REASON -> PROPOSE."""
    item = stage_ingest(item)
    item = stage_extract(item)
    item = stage_reason(item, config)
    item = stage_propose(item, config)
    item.stage = "processed"
    return item
