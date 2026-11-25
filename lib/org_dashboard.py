#!/usr/bin/env python3
"""
GRIP Org Dashboard — HTML visualization of org topology and brain merge state.

Generates a self-contained HTML file and opens it in the browser.

Usage:
  python3 lib/org_dashboard.py           # Generate and open dashboard
  python3 lib/org_dashboard.py --output /path/to/file.html  # Custom output path
  python3 lib/org_dashboard.py --json    # Print JSON data only (for metrics-server)

CLI command: /org gui
"""

import json
import os
import sys
import webbrowser
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
ORGS_DIR = CLAUDE_DIR / "organizations"
DEFAULT_OUTPUT = CLAUDE_DIR / "cache" / "org-dashboard.html"

CONSCIOUSNESS_LABELS = {
    0: ("LATENT", "#6b7280"),
    1: ("DETECTED", "#f59e0b"),
    2: ("UNDERSTOOD", "#3b82f6"),
    3: ("CODIFIED", "#8b5cf6"),
    4: ("EMBODIED", "#10b981"),
}


def _collect_org_data() -> list:
    """Collect org data from organization directories."""
    orgs = []
    if not ORGS_DIR.is_dir():
        return orgs
    for org_dir in sorted(ORGS_DIR.iterdir()):
        if not org_dir.is_dir():
            continue
        profile_path = org_dir / "profile.json"
        if not profile_path.exists():
            continue
        try:
            profile = json.loads(profile_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        connectors = profile.get("connectors", [])
        has_github = any(c.get("type") == "github" for c in connectors)
        has_slack = any(c.get("type") == "slack" for c in connectors)
        # Calculate consciousness level
        active_org = os.environ.get("GRIP_ORG", "default")
        w = 0
        if profile.get("slug"):
            w = 1
        if profile.get("team") or profile.get("description"):
            w = 2
        if has_github and has_slack:
            w = 3
        if os.environ.get("GRIP_AUTONOMOUS", "0") >= "1" and has_github and has_slack:
            w = 4
        orgs.append({
            "slug": profile.get("slug", org_dir.name),
            "name": profile.get("name", org_dir.name),
            "consciousness": w,
            "connectors": connectors,
            "has_github": has_github,
            "has_slack": has_slack,
            "incognito": profile.get("incognito", False),
            "active": profile.get("slug") == active_org,
        })
    return orgs


def _collect_merge_data() -> list:
    """Collect brain merge data."""
    merges_file = CLAUDE_DIR / "cache" / "brain-merges.json"
    if not merges_file.exists():
        return []
    try:
        return json.loads(merges_file.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def generate_org_html(orgs: Optional[list] = None, merges: Optional[list] = None) -> str:
    """Generate self-contained HTML dashboard."""
    if orgs is None:
        orgs = _collect_org_data()
    if merges is None:
        merges = _collect_merge_data()

    orgs_json = json.dumps(orgs)
    merges_json = json.dumps(merges)
    consciousness_json = json.dumps({str(k): list(v) for k, v in CONSCIOUSNESS_LABELS.items()})

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GRIP Org Dashboard</title>
<style>
  :root {{
    --bg: #0f1117; --surface: #1a1d27; --border: #2d3147;
    --text: #e2e8f0; --muted: #64748b; --radius: 12px;
    --accent: #6366f1;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'SF Mono', 'Fira Code', monospace; font-size: 13px; padding: 24px; }}
  h1 {{ font-size: 18px; font-weight: 600; margin-bottom: 24px; color: var(--accent); }}
  h2 {{ font-size: 14px; font-weight: 600; margin-bottom: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; margin-bottom: 32px; }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; }}
  .card.active {{ border-color: var(--accent); }}
  .card-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }}
  .org-name {{ font-size: 15px; font-weight: 600; }}
  .badge {{ padding: 3px 8px; border-radius: 20px; font-size: 11px; font-weight: 600; }}
  .connector {{ display: inline-flex; align-items: center; gap: 4px; margin-right: 8px; margin-top: 8px; padding: 3px 8px; border-radius: 6px; background: #1e2030; font-size: 11px; }}
  .connector.active {{ background: #1a2e1a; color: #4ade80; }}
  .connector.inactive {{ color: var(--muted); }}
  .merges-table {{ width: 100%; border-collapse: collapse; }}
  .merges-table th, .merges-table td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border); }}
  .merges-table th {{ color: var(--muted); font-weight: 500; }}
  .status-active {{ color: #4ade80; }}
  .status-pending {{ color: #f59e0b; }}
  .status-rejected {{ color: #f87171; }}
  .tag-active {{ background: #1e3a1e; color: #4ade80; padding: 2px 6px; border-radius: 4px; font-size: 10px; }}
  .no-data {{ color: var(--muted); padding: 12px 0; }}
</style>
</head>
<body>
<h1>GRIP Org Dashboard</h1>

<h2>Organizations</h2>
<div class="grid" id="orgs-grid"></div>

<h2>Brain Merges</h2>
<div id="merges-section"></div>

<script>
const ORGS = {orgs_json};
const MERGES = {merges_json};
const CONSCIOUSNESS = {consciousness_json};

function renderOrgs() {{
  const grid = document.getElementById('orgs-grid');
  if (!ORGS.length) {{ grid.innerHTML = '<p class="no-data">No organizations found.</p>'; return; }}
  ORGS.forEach(org => {{
    const [label, color] = CONSCIOUSNESS[String(org.consciousness)] || ['UNKNOWN', '#6b7280'];
    const card = document.createElement('div');
    card.className = 'card' + (org.active ? ' active' : '');
    card.innerHTML = `
      <div class="card-header">
        <span class="org-name">${{org.name}}${{org.active ? ' <span class="tag-active">ACTIVE</span>' : ''}}</span>
        <span class="badge" style="background: ${{color}}22; color: ${{color}}">W${{org.consciousness}} ${{label}}</span>
      </div>
      <div style="color: var(--muted); font-size: 11px; margin-bottom: 8px">${{org.slug}}${{org.incognito ? ' . incognito' : ''}}</div>
      <div>
        <span class="connector ${{org.has_github ? 'active' : 'inactive'}}">${{org.has_github ? 'v' : 'o'}} GitHub</span>
        <span class="connector ${{org.has_slack ? 'active' : 'inactive'}}">${{org.has_slack ? 'v' : 'o'}} Slack</span>
      </div>
    `;
    grid.appendChild(card);
  }});
}}

function renderMerges() {{
  const section = document.getElementById('merges-section');
  if (!MERGES.length) {{ section.innerHTML = '<p class="no-data">No merge requests.</p>'; return; }}
  const table = document.createElement('table');
  table.className = 'merges-table';
  table.innerHTML = '<thead><tr><th>ID</th><th>From</th><th>To</th><th>Type</th><th>Status</th><th>Weight</th></tr></thead>';
  const tbody = document.createElement('tbody');
  MERGES.forEach(m => {{
    const tr = document.createElement('tr');
    const statusClass = m.status === 'active' ? 'status-active' : m.status === 'pending' ? 'status-pending' : 'status-rejected';
    tr.innerHTML = `
      <td style="color: var(--muted)">${{m.request_id}}</td>
      <td>${{m.requesting_namespace}}</td>
      <td>${{m.target_namespace}}</td>
      <td style="color: var(--muted)">${{m.merge_type}}</td>
      <td class="${{statusClass}}">${{m.status}}</td>
      <td>${{m.weight_override}}</td>
    `;
    tbody.appendChild(tr);
  }});
  table.appendChild(tbody);
  section.appendChild(table);
}}

renderOrgs();
renderMerges();
</script>
</body>
</html>"""


def open_dashboard(html_out: Optional[Path] = None) -> Path:
    """Generate HTML and open in browser."""
    html = generate_org_html()
    out = html_out or DEFAULT_OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    webbrowser.open("file://" + str(out.resolve()))
    return out


def serve_dashboard(port: int = 8766) -> None:
    """Serve dashboard via HTTP (extends metrics-server pattern)."""
    from http.server import HTTPServer, BaseHTTPRequestHandler

    html = generate_org_html()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/org"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode())
            elif self.path == "/org/api":
                data = {"orgs": _collect_org_data(), "merges": _collect_merge_data()}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass  # Suppress request logs

    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"Org dashboard: http://localhost:{port}/org")
    server.serve_forever()


def main() -> None:
    args = sys.argv[1:]
    if "--json" in args:
        print(json.dumps({"orgs": _collect_org_data(), "merges": _collect_merge_data()}, indent=2))
        return
    if "--serve" in args:
        port = 8766
        if "--port" in args:
            idx = args.index("--port")
            port = int(args[idx + 1])
        serve_dashboard(port)
        return
    out_path = None
    if "--output" in args:
        idx = args.index("--output")
        out_path = Path(args[idx + 1])
    out = open_dashboard(out_path)
    print(f"Dashboard: {out}")


if __name__ == "__main__":
    main()
