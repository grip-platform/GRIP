#!/usr/bin/env python3
"""
GRIP Calendar fetcher — gcal.py
Fetches Google Calendar events for a workspace user via Domain-Wide Delegation.
Auto-detects a Python environment with google-auth; re-execs into it if needed.

SA: grip-meet-absorb-sa@your-gcp-project-id.iam.gserviceaccount.com
DWD scope: https://www.googleapis.com/auth/calendar.readonly
ADC: laurie@ must be active (`gcloud auth application-default login`)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

SA = "grip-meet-absorb-sa@your-gcp-project-id.iam.gserviceaccount.com"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
_REEXEC_FLAG = "__GCAL_REEXECED__"


def _has_google_modules() -> bool:
    try:
        import google.auth  # noqa: F401
        import google.auth.impersonated_credentials  # noqa: F401
        from googleapiclient.discovery import build  # noqa: F401
        return True
    except ImportError:
        return False


def _find_python_with_google() -> str | None:
    candidates: list[str] = [sys.executable]
    for name in ("python3", "python"):
        p = shutil.which(name)
        if p:
            candidates.append(p)
    for ver in ("3.13", "3.12", "3.11", "3.10", "3.9"):
        candidates.append(f"/opt/homebrew/opt/python@{ver}/bin/python{ver}")
    candidates += ["/usr/bin/python3", "/usr/local/bin/python3", "/usr/local/bin/python"]

    seen: set[str] = set()
    for py in candidates:
        if py in seen:
            continue
        seen.add(py)
        try:
            r = subprocess.run(
                [py, "-c", "import google.auth, googleapiclient"],
                capture_output=True,
                timeout=5,
            )
            if r.returncode == 0:
                return py
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def _fetch_events(subject: str, days: int) -> list[dict]:
    import google.auth
    import google.auth.impersonated_credentials as ic
    from googleapiclient.discovery import build

    source, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds = ic.Credentials(
        source_credentials=source,
        target_principal=SA,
        target_scopes=SCOPES,
        delegates=[],
        subject=subject,
    )
    svc = build("calendar", "v3", credentials=creds, cache_discovery=False)

    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)

    result = svc.events().list(
        calendarId="primary",
        timeMin=now.isoformat(),
        timeMax=end.isoformat(),
        maxResults=50,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = []
    for e in result.get("items", []):
        start = e["start"].get("dateTime") or e["start"].get("date", "")
        end_t = e["end"].get("dateTime") or e["end"].get("date", "")
        all_day = "dateTime" not in e["start"]
        summary = e.get("summary", "(no title)")
        # Skip bare location markers (e.g. all-day "Home" events)
        if all_day and summary in ("Home", "Office", "WFH", "Away", "OOO"):
            continue
        events.append({
            "summary": summary,
            "start": start,
            "end": end_t,
            "all_day": all_day,
            "location": e.get("location", ""),
            "attendees": [a.get("email", "") for a in e.get("attendees", [])],
            "description": (e.get("description") or "")[:300],
            "html_link": e.get("htmlLink", ""),
        })
    return events


def main() -> None:
    import argparse

    # Guard against infinite re-exec
    if os.environ.get(_REEXEC_FLAG) != "1" and not _has_google_modules():
        py = _find_python_with_google()
        if py is None:
            print(json.dumps({
                "error": (
                    "google-auth not found in any Python environment. "
                    "Install with: pip install google-auth google-api-python-client"
                )
            }))
            sys.exit(1)
        env = {**os.environ, _REEXEC_FLAG: "1"}
        os.execve(py, [py] + sys.argv, env)

    parser = argparse.ArgumentParser(description="Fetch Google Calendar events via DWD")
    parser.add_argument("subject", help="Calendar owner email (e.g. licensing@codetonight.co.za)")
    parser.add_argument("--days", type=int, default=7, help="Days ahead to fetch (default: 7)")
    args = parser.parse_args()

    try:
        events = _fetch_events(args.subject, args.days)
        print(json.dumps({"subject": args.subject, "days": args.days, "events": events}, indent=2))
    except Exception as exc:
        print(json.dumps({"error": str(exc), "subject": args.subject}))
        sys.exit(1)


if __name__ == "__main__":
    main()
