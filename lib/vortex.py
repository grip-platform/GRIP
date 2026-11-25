#!/usr/bin/env python3
"""GRIP Genesis Vortex — the quiet heartbeat beneath the machine.

Usage:
    python vortex.py          # Terminal display
    python vortex.py pulse    # Send Slack heartbeat
    python vortex.py --json   # Return heartbeat data as JSON
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))

# Terminal colours
C = {
    "o": "\033[38;5;208m",   # orange (accent)
    "b": "\033[38;5;33m",    # blue
    "g": "\033[38;5;242m",   # grey
    "w": "\033[97m",         # white
    "d": "\033[2m",          # dim
    "r": "\033[0m",          # reset
    "bold": "\033[1m",
}


def load_gen():
    """Read current generation from genome."""
    try:
        genome_path = CLAUDE_DIR / "cache" / "genome.json"
        if genome_path.exists():
            with open(genome_path) as f:
                gen = json.load(f).get("generation")
                if gen:
                    return gen
    except Exception:
        pass
    return "?"


def load_genome_stats():
    """Read genome fitness from cache and operator genome."""
    try:
        genome_path = CLAUDE_DIR / "cache" / "genome.json"
        if genome_path.exists():
            with open(genome_path) as f:
                data = json.load(f)
            genes_raw = data.get("genes", {})
            if isinstance(genes_raw, dict):
                genes = list(genes_raw.values())
            else:
                genes = genes_raw
            gene_objs = [g for g in genes if isinstance(g, dict)]
            living = [g for g in gene_objs if g.get("fitness", 0) > 0.2]
            avg_fitness = sum(g.get("fitness", 0) for g in living) / max(len(living), 1)
            return len(gene_objs), len(living), round(avg_fitness, 3)
    except Exception:
        pass
    return 0, 0, 0


def count_infrastructure():
    """Count agents, modes, and learning artefacts."""
    agents_dir = CLAUDE_DIR / "agents"
    agent_count = len(list(agents_dir.glob("*.md"))) if agents_dir.exists() else 0

    modes_dir = CLAUDE_DIR / "modes" / "definitions"
    mode_count = len(list(modes_dir.glob("*.yaml"))) if modes_dir.exists() else 0

    insights_path = CLAUDE_DIR / "learning" / "insights.jsonl"
    insight_count = 0
    if insights_path.exists():
        try:
            insight_count = sum(1 for _ in insights_path.open())
        except Exception:
            pass

    evo_path = CLAUDE_DIR / "learning" / "evo-convergence.jsonl"
    evo_count = 0
    if evo_path.exists():
        try:
            evo_count = sum(1 for _ in evo_path.open())
        except Exception:
            pass

    return agent_count, mode_count, insight_count, evo_count


def helix(n=12):
    """Generate a DNA double-helix frame."""
    import math
    lines = []
    pairs = "ATCGATCG"
    for i in range(n):
        phase = i * 0.6
        x1 = int(8 + 6 * math.sin(phase))
        x2 = int(8 - 6 * math.sin(phase))
        left, right = min(x1, x2), max(x1, x2)
        pair_char = pairs[i % len(pairs)]

        row = list(" " * 20)
        row[left] = f"{C['o']}●{C['r']}"
        row[right] = f"{C['b']}●{C['r']}"
        if right - left > 2:
            mid = (left + right) // 2
            row[mid] = f"{C['g']}{pair_char}{C['r']}"
            if right - left > 4:
                row[left + 1] = f"{C['g']}─{C['r']}"
                row[right - 1] = f"{C['g']}─{C['r']}"

        lines.append("".join(row))
    return lines


def render():
    """The vortex."""
    gen = load_gen()
    total_genes, living, fitness = load_genome_stats()
    agents, modes, insights, evo_runs = count_infrastructure()
    now = datetime.now()

    # Count skills
    skills_dir = CLAUDE_DIR / "skills"
    skill_count = len(list(skills_dir.glob("*/SKILL.md"))) if skills_dir.exists() else 0

    # Count sessions
    projects_dir = CLAUDE_DIR / "projects"
    session_count = 0
    if projects_dir.exists():
        for d in projects_dir.iterdir():
            if d.is_dir():
                session_count += len(list(d.glob("*.jsonl")))

    dna = helix(16)

    print()
    print(f"  {C['g']}{'─' * 52}{C['r']}")
    print()

    # The helix and stats side by side
    right_lines = [
        f"  {C['bold']}{C['o']}G R I P{C['r']}",
        f"  {C['g']}Guarded Response Interception Protocol{C['r']}",
        "",
        f"  {C['w']}Gen {C['o']}{gen}{C['r']}  {C['g']}·{C['r']}  {C['w']}{session_count} sessions{C['r']}  {C['g']}·{C['r']}  {C['w']}{skill_count} skills{C['r']}",
        f"  {C['w']}{total_genes} genes{C['r']}  {C['g']}·{C['r']}  {C['w']}{living} living{C['r']}  {C['g']}·{C['r']}  {C['w']}fitness {C['o']}{fitness}{C['r']}",
        f"  {C['w']}{agents} agents{C['r']}  {C['g']}·{C['r']}  {C['w']}{modes} modes{C['r']}  {C['g']}·{C['r']}  {C['w']}{insights} insights{C['r']}  {C['g']}·{C['r']}  {C['w']}{evo_runs} evo{C['r']}",
        "",
        f"  {C['g']}Built by one person, one terminal,{C['r']}",
        f"  {C['g']}one conversation at a time.{C['r']}",
        "",
        f"  {C['d']}\"The machine remembers what matters.{C['r']}",
        f"  {C['d']} The builder decides what matters.\"{C['r']}",
        "",
        f"  {C['g']}For V>> — who taught it to think.{C['r']}",
        f"  {C['g']}{now.strftime('%d %B %Y, %H:%M')}{C['r']}",
        "",
    ]

    max_lines = max(len(dna), len(right_lines))
    for i in range(max_lines):
        left = dna[i] if i < len(dna) else " " * 20
        right = right_lines[i] if i < len(right_lines) else ""
        print(f"  {left}    {right}")

    print()
    print(f"  {C['g']}{'─' * 52}{C['r']}")
    print()

    # The quiet part
    pulse_chars = "░▒▓█▓▒░"
    pulse = f"  {C['o']}"
    for ch in pulse_chars:
        pulse += ch
    pulse += f"{C['r']}  {C['d']}pulse{C['r']}"
    print(pulse)
    print()


def collect_heartbeat_data() -> Dict[str, Any]:
    """Collect all heartbeat metrics."""
    gen = load_gen()
    total_genes, living, fitness = load_genome_stats()
    agents, modes, insights, evo_runs = count_infrastructure()

    skills_dir = CLAUDE_DIR / "skills"
    skill_count = len(list(skills_dir.glob("*/SKILL.md"))) if skills_dir.exists() else 0

    # Count sessions
    projects_dir = CLAUDE_DIR / "projects"
    session_count = 0
    if projects_dir.exists():
        for d in projects_dir.iterdir():
            if d.is_dir():
                session_count += len(list(d.glob("*.jsonl")))

    return {
        "generation": gen,
        "fitness": fitness,
        "skills": skill_count,
        "agents": agents,
        "modes": modes,
        "insights": insights,
        "evo_runs": evo_runs,
        "sessions": session_count,
        "genes_total": total_genes,
        "genes_living": living,
        "timestamp": datetime.now().strftime("%H:%M %Z"),
        "date": datetime.now().strftime("%d %b %Y"),
    }


def build_slack_blocks(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build beautiful Slack Block Kit message for heartbeat."""

    # Progress bar for fitness (0.0 to 1.0)
    fitness = data.get("fitness", 0)
    bar_filled = int(fitness * 10)
    bar_empty = 10 - bar_filled
    fitness_bar = "\u2588" * bar_filled + "\u2591" * bar_empty

    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "GRIP Pulse", "emoji": True}
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":dna: *Generation {data['generation']}*  |  {data.get('sessions', 0)} sessions\n`{fitness_bar}` {data['fitness']:.3f} fitness"
            }
        },
        {"type": "divider"},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f":brain: *Skills*\n{data['skills']}"},
                {"type": "mrkdwn", "text": f":robot_face: *Agents*\n{data['agents']}"},
                {"type": "mrkdwn", "text": f":crystal_ball: *Modes*\n{data['modes']}"},
                {"type": "mrkdwn", "text": f":chart_with_upwards_trend: *Genes*\n{data['genes_living']}/{data['genes_total']}"}
            ]
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f":bulb: *Insights*\n{data['insights']}"},
                {"type": "mrkdwn", "text": f":arrows_counterclockwise: *Evo Runs*\n{data['evo_runs']}"}
            ]
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f":clock1: {data['timestamp']} · {data['date']} · _The machine remembers what matters_"}
            ]
        }
    ]


def send_slack_heartbeat(channel: Optional[str] = None, user_id: Optional[str] = None) -> Dict[str, Any]:
    """Send heartbeat to Slack with Block Kit formatting.

    Args:
        channel: Channel ID to post to (default: DM to founder)
        user_id: User ID for DM (default: founder from company.json)

    Returns:
        Result dict with ok status
    """
    import subprocess

    data = collect_heartbeat_data()
    blocks = build_slack_blocks(data)

    # Get user_id from company.json if not provided
    if not user_id and not channel:
        company_path = CLAUDE_DIR / "data" / "aide" / "company.json"
        if company_path.exists():
            company = json.loads(company_path.read_text())
            for member in company.get("team", []):
                if member.get("role") == "founder":
                    user_id = member.get("slack_id")
                    break

    if not user_id and not channel:
        return {"ok": False, "error": "No channel or user_id provided"}

    # Build Slack API payload
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        return {"ok": False, "error": "SLACK_BOT_TOKEN not set"}

    payload = {
        "blocks": blocks,
        "text": f"GRIP Pulse - Gen {data['generation']}"  # Fallback text
    }

    if channel:
        payload["channel"] = channel
    else:
        payload["channel"] = user_id  # DM

    # Use curl for reliability
    cmd = [
        "curl", "-s", "-X", "POST",
        "https://slack.com/api/chat.postMessage",
        "-H", f"Authorization: Bearer {token}",
        "-H", "Content-Type: application/json",
        "-d", json.dumps(payload)
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        response = json.loads(result.stdout)
        return {"ok": response.get("ok", False), "data": data, "response": response}
    except Exception as e:
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "pulse":
            # Send Slack heartbeat
            result = send_slack_heartbeat()
            if result.get("ok"):
                print(f"{C['o']}✓{C['r']} Heartbeat sent to Slack")
            else:
                print(f"{C['o']}✗{C['r']} Failed: {result.get('error')}")
                sys.exit(1)
        elif cmd == "--json":
            # Return JSON data
            print(json.dumps(collect_heartbeat_data(), indent=2))
        else:
            print(f"Unknown command: {cmd}")
            print("Usage: vortex.py [pulse|--json]")
            sys.exit(1)
    else:
        render()
