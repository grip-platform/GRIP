#!/usr/bin/env python3
"""GRIP Daily Tips - Sends one GRIP feature tip per day to team members via Slack."""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import argparse

CLAUDE_DIR = Path(os.environ.get('CLAUDE_DIR', str(Path.home() / '.claude')))
TIPS_DIR = CLAUDE_DIR / 'data' / 'daily-tips'
TEAMS_DIR = TIPS_DIR / 'teams'
LOG_DIR = CLAUDE_DIR / 'logs'
ENV_FILE = CLAUDE_DIR / 'mcp-servers' / 'grip-mcp' / '.env'


def log(msg):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / 'daily-tips.log'
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line)
    with open(log_file, 'a') as f:
        f.write(line + '\n')


def load_env_token():
    if not ENV_FILE.exists():
        log(f'ERROR: .env not found at {ENV_FILE}')
        sys.exit(1)
    with open(ENV_FILE) as f:
        for line in f:
            if line.startswith('SLACK_BOT_TOKEN='):
                return line.strip().split('=', 1)[1]
    log('ERROR: SLACK_BOT_TOKEN not found in .env')
    sys.exit(1)


def load_tips():
    tips_file = TIPS_DIR / 'tips.json'
    if not tips_file.exists():
        log(f'ERROR: tips.json not found at {tips_file}')
        sys.exit(1)
    with open(tips_file) as f:
        return json.load(f)


def load_teams(team_filter=None):
    teams = []
    if not TEAMS_DIR.exists():
        log(f'ERROR: teams directory not found at {TEAMS_DIR}')
        sys.exit(1)
    for fp in sorted(TEAMS_DIR.glob('*.json')):
        with open(fp) as fh:
            team = json.load(fh)
            team['_file'] = str(fp)
            if team_filter is None or team_filter == fp.stem:
                teams.append(team)
    return teams


def atomic_write_json(path, data):
    dir_path = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=dir_path, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=2)
            f.write('\n')
        os.replace(tmp, path)
    except Exception:
        os.unlink(tmp)
        raise


def send_slack_dm(token, channel_id, text):
    blocks = []
    for section in text.split("\n\n"):
        section = section.strip()
        if section:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": section[:3000]}})
    msg_data = {'channel': channel_id, 'text': text}
    if blocks:
        msg_data['blocks'] = blocks
    payload = json.dumps(msg_data).encode('utf-8')
    req = urllib.request.Request(
        'https://slack.com/api/chat.postMessage',
        data=payload,
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json; charset=utf-8'
        }
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read().decode('utf-8'))
        if not result.get('ok'):
            log(f'  Slack API error: {result.get("error", "unknown")}')
            return False
        return True
    except urllib.error.URLError as e:
        log(f'  Network error: {e}')
        return False


def format_tip(tip, total_tips, next_topic=None):
    parts = [
        f':bulb: *GRIP Tip of the Day* (Day {tip["day"]}/{total_tips})',
        '',
        f'*{tip["topic"]}*',
        '',
        tip['message'],
        '',
        f':point_right: Try it: `{tip["try_it"]}`',
        f':books: Docs: `~/.claude/{tip["docs"]}`',
    ]
    if next_topic:
        parts.extend(['', f'_Tomorrow: {next_topic}_'])
    return '\n'.join(parts)


def send_tip(channel_type, token, recipient_id, message):
    if channel_type == 'slack':
        return send_slack_dm(token, recipient_id, message)
    elif channel_type == 'email':
        log('  Email channel not yet implemented')
        return False
    else:
        log(f'  Unknown channel type: {channel_type}')
        return False


def main():
    parser = argparse.ArgumentParser(description='GRIP Daily Tips sender')
    parser.add_argument('--dry-run', action='store_true', help='Print messages without sending')
    parser.add_argument('--team', type=str, default=None, help='Send to specific team only')
    parser.add_argument('--force-tip', type=int, default=None, help='Send specific tip number (1-30)')
    args = parser.parse_args()

    log('--- Daily Tips run started ---')

    token = load_env_token()
    tips_data = load_tips()
    tips = tips_data['tips']
    total_tips = tips_data['total_tips']
    teams = load_teams(args.team)

    if not teams:
        log('No teams found (or filtered team does not exist)')
        return

    for team in teams:
        team_name = team['team_name']
        channel_type = team.get('channel', 'slack')
        tip_index = team['current_tip_index']

        if args.force_tip is not None:
            tip_index = args.force_tip - 1

        if tip_index >= total_tips:
            log(f'[{team_name}] Series complete ({total_tips} tips sent). Skipping.')
            continue

        tip = tips[tip_index]
        next_topic = tips[tip_index + 1]['topic'] if tip_index + 1 < total_tips else None
        message = format_tip(tip, total_tips, next_topic)

        log(f'[{team_name}] Sending tip {tip["day"]}/{total_tips}: {tip["topic"]}')

        if args.dry_run:
            log(f'[DRY RUN] Would send to {len(team["members"])} members:')
            print(message)
            print('---')
            continue

        success_count = 0
        for member in team['members']:
            member_id = member.get('slack_id') or member.get('email', 'unknown')
            log(f'  Sending to {member["name"]} ({member_id})')
            if send_tip(channel_type, token, member_id, message):
                success_count += 1
                log(f'  Delivered to {member["name"]}')
            else:
                log(f'  FAILED for {member["name"]}')

        log(f'[{team_name}] Delivered to {success_count}/{len(team["members"])} members')

        if args.force_tip is None and not args.dry_run:
            team['current_tip_index'] = tip_index + 1
            if team['started_at'] is None:
                team['started_at'] = datetime.now(timezone.utc).isoformat()
            team_file = team.pop('_file')
            atomic_write_json(team_file, team)
            log(f'[{team_name}] State updated: tip_index={tip_index + 1}')
        elif '_file' in team:
            team.pop('_file')

    log('--- Daily Tips run complete ---')


if __name__ == '__main__':
    main()
