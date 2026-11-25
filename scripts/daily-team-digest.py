#!/usr/bin/env python3
"""GRIP Team Digest - Sends end-of-day channel digests to team members via Slack."""

import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import argparse

CLAUDE_DIR = Path(os.environ.get('CLAUDE_DIR', str(Path.home() / '.claude')))
DIGEST_DIR = CLAUDE_DIR / 'data' / 'team-digest'
TEAMS_DIR = DIGEST_DIR / 'teams'
LOG_DIR = CLAUDE_DIR / 'logs'
ENV_FILE = CLAUDE_DIR / 'mcp-servers' / 'grip-mcp' / '.env'


def log(msg):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / 'team-digest.log'
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


def slack_api_get(token, method, params):
    """Make a Slack API GET request with query params."""
    query = urllib.parse.urlencode(params)
    url = f'https://slack.com/api/{method}?{query}'
    req = urllib.request.Request(
        url,
        headers={'Authorization': f'Bearer {token}'}
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return json.loads(resp.read().decode('utf-8'))
    except urllib.error.URLError as e:
        log(f'  Network error ({method}): {e}')
        return {'ok': False, 'error': str(e)}


def fetch_channel_history(token, channel_id, oldest_ts, max_messages=20):
    """Fetch channel messages from the last 24h, paginated."""
    messages = []
    cursor = None
    while len(messages) < max_messages:
        params = {
            'channel': channel_id,
            'oldest': str(oldest_ts),
            'limit': min(200, max_messages - len(messages)),
        }
        if cursor:
            params['cursor'] = cursor

        result = slack_api_get(token, 'conversations.history', params)
        if not result.get('ok'):
            log(f'  conversations.history error: {result.get("error", "unknown")}')
            break

        batch = result.get('messages', [])
        messages.extend(batch)

        metadata = result.get('response_metadata', {})
        cursor = metadata.get('next_cursor')
        if not cursor or not batch:
            break

    # API returns newest-first, reverse for chronological order
    messages.reverse()
    return messages[:max_messages]


def filter_messages(messages, exclude_user_id):
    """Exclude bot messages, system messages, and recipient's own messages."""
    filtered = []
    for msg in messages:
        # Skip bot messages
        if msg.get('bot_id') or msg.get('subtype') == 'bot_message':
            continue
        # Skip system/join/leave/topic messages
        subtype = msg.get('subtype', '')
        if subtype in ('channel_join', 'channel_leave', 'channel_topic',
                        'channel_purpose', 'channel_name', 'channel_archive',
                        'channel_unarchive', 'pinned_item', 'unpinned_item'):
            continue
        # Skip recipient's own messages
        if msg.get('user') == exclude_user_id:
            continue
        # Skip empty messages
        text = msg.get('text', '').strip()
        if not text:
            continue
        filtered.append(msg)
    return filtered


def resolve_user_names(token, user_ids):
    """Map user IDs to display names, cached per run."""
    names = {}
    for uid in user_ids:
        result = slack_api_get(token, 'users.info', {'user': uid})
        if result.get('ok'):
            user = result['user']
            name = (user.get('profile', {}).get('display_name')
                    or user.get('profile', {}).get('real_name')
                    or user.get('real_name')
                    or user.get('name', uid))
            names[uid] = name
        else:
            names[uid] = uid
    return names


def build_slack_deeplink(workspace, channel_id, message_ts):
    """Construct Slack deep link URL from message timestamp."""
    ts_no_dot = message_ts.replace('.', '')
    return f'https://{workspace}.slack.com/archives/{channel_id}/p{ts_no_dot}'


def format_message_time(ts_str, tz_offset_hours=2):
    """Format message timestamp to local HH:MM."""
    ts = float(ts_str)
    utc_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    local_dt = utc_dt + timedelta(hours=tz_offset_hours)
    return local_dt.strftime('%H:%M')


def get_tz_offset(tz_name):
    """Simple timezone offset lookup for African timezones."""
    offsets = {
        'Africa/Johannesburg': 2,
        'Africa/Dar_es_Salaam': 3,
        'Africa/Cairo': 2,
        'Africa/Lagos': 1,
        'Africa/Nairobi': 3,
        'UTC': 0,
    }
    return offsets.get(tz_name, 2)


def format_digest(team_name, workspace, channel_summaries, date_str, tz_offset):
    """Build the Slack mrkdwn digest message."""
    parts = [
        f':crescent_moon: *End-of-Day Digest* - {date_str}',
    ]

    for ch in channel_summaries:
        parts.append('')
        parts.append('---')

        if not ch['messages']:
            parts.append(f'_No activity in #{ch["name"]} today._')
            continue

        count = len(ch['messages'])
        parts.append(f':speech_balloon: *#{ch["name"]}* ({count} message{"s" if count != 1 else ""})')
        parts.append('')

        for msg in ch['messages']:
            sender = msg['_display_name']
            time_str = format_message_time(msg['ts'], tz_offset)
            text = msg.get('text', '').strip()
            # Truncate to 120 chars
            if len(text) > 120:
                text = text[:117] + '...'
            link = build_slack_deeplink(workspace, ch['id'], msg['ts'])
            parts.append(f'> *{sender}* ({time_str}): {text}')
            parts.append(f'> <{link}|View>')
            parts.append('')

    parts.append('---')
    sign_off_hour = 22
    tz_label = 'CAT' if tz_offset == 2 else f'UTC+{tz_offset}'
    if tz_offset == 2:
        tz_label = 'SAST'
    elif tz_offset == 3:
        tz_label = 'EAT'
    parts.append(f':zzz: _Digest at {sign_off_hour}:00 {tz_label}_')

    return '\n'.join(parts)


def format_digest_blocks(team_name, workspace, channel_summaries, date_str, tz_offset) -> list:
    """Format digest as Slack Block Kit blocks."""
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
        from aide.integrations import text_to_blocks
        return text_to_blocks(format_digest(team_name, workspace, channel_summaries, date_str, tz_offset))
    except (ImportError, Exception):
        text = format_digest(team_name, workspace, channel_summaries, date_str, tz_offset)
        return [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]


def send_slack_dm(token, user_id, text):
    """Open a DM channel and send the digest."""
    # Open DM conversation
    open_payload = json.dumps({'users': user_id}).encode('utf-8')
    req = urllib.request.Request(
        'https://slack.com/api/conversations.open',
        data=open_payload,
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json; charset=utf-8'
        }
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read().decode('utf-8'))
        if not result.get('ok'):
            log(f'  conversations.open error: {result.get("error", "unknown")}')
            return False
        dm_channel = result['channel']['id']
    except urllib.error.URLError as e:
        log(f'  Network error (conversations.open): {e}')
        return False

    # Build Block Kit blocks for rich Slack rendering
    blocks = []
    for section in text.split("\n\n"):
        section = section.strip()
        if section:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": section[:3000]}})

    # Send message to DM channel
    msg_data = {'channel': dm_channel, 'text': text}
    if blocks:
        msg_data['blocks'] = blocks
    msg_payload = json.dumps(msg_data).encode('utf-8')
    req = urllib.request.Request(
        'https://slack.com/api/chat.postMessage',
        data=msg_payload,
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json; charset=utf-8'
        }
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read().decode('utf-8'))
        if not result.get('ok'):
            log(f'  chat.postMessage error: {result.get("error", "unknown")}')
            return False
        return True
    except urllib.error.URLError as e:
        log(f'  Network error (chat.postMessage): {e}')
        return False


def main():
    parser = argparse.ArgumentParser(description='GRIP Team Digest sender')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print digests without sending')
    parser.add_argument('--team', type=str, default=None,
                        help='Send to specific team only')
    parser.add_argument('--force-date', type=str, default=None,
                        help='Override date (YYYY-MM-DD)')
    args = parser.parse_args()

    log('--- Team Digest run started ---')

    token = load_env_token()
    teams = load_teams(args.team)

    if not teams:
        log('No teams found (or filtered team does not exist)')
        return

    # Calculate 24h window
    now = datetime.now(timezone.utc)
    oldest_ts = (now - timedelta(hours=24)).timestamp()

    # Date string for digest header
    if args.force_date:
        try:
            forced = datetime.strptime(args.force_date, '%Y-%m-%d')
            date_str = forced.strftime('%A, %b %d')
        except ValueError:
            log(f'ERROR: Invalid date format: {args.force_date}. Use YYYY-MM-DD.')
            sys.exit(1)
    else:
        date_str = now.strftime('%A, %b %d')

    for team in teams:
        team_name = team['team_name']
        workspace = team['workspace']
        monitored = team.get('monitored_channels', [])
        max_per_channel = team.get('max_messages_per_channel', 20)

        log(f'[{team_name}] Processing {len(monitored)} channels')

        # Collect all unique user IDs across channels for name resolution
        all_user_ids = set()
        channel_raw = {}

        for ch in monitored:
            log(f'  Fetching #{ch["name"]} ({ch["id"]})')
            messages = fetch_channel_history(token, ch['id'], oldest_ts, max_per_channel)
            channel_raw[ch['id']] = messages
            for msg in messages:
                uid = msg.get('user')
                if uid:
                    all_user_ids.add(uid)

        # Resolve all user names in one batch
        if all_user_ids:
            log(f'  Resolving {len(all_user_ids)} user names')
            user_names = resolve_user_names(token, all_user_ids)
        else:
            user_names = {}

        # Build per-member digests
        for member in team['members']:
            member_name = member['name']
            member_id = member['slack_id']
            tz_offset = get_tz_offset(member.get('timezone', 'Africa/Johannesburg'))

            # Filter and annotate messages per channel
            channel_summaries = []
            total_msgs = 0
            for ch in monitored:
                raw = channel_raw.get(ch['id'], [])
                filtered = filter_messages(raw, member_id)
                # Annotate with display names
                for msg in filtered:
                    msg['_display_name'] = user_names.get(msg.get('user', ''), 'Unknown')
                channel_summaries.append({
                    'id': ch['id'],
                    'name': ch['name'],
                    'messages': filtered,
                })
                total_msgs += len(filtered)

            if total_msgs == 0:
                log(f'  [{member_name}] No activity to digest, skipping')
                continue

            digest = format_digest(team_name, workspace, channel_summaries,
                                   date_str, tz_offset)

            log(f'  [{member_name}] {total_msgs} messages across {len(monitored)} channels')

            if args.dry_run:
                log(f'  [DRY RUN] Would send to {member_name}:')
                print(digest)
                print('---')
                continue

            if send_slack_dm(token, member_id, digest):
                log(f'  Delivered to {member_name}')
            else:
                log(f'  FAILED for {member_name}')

        # Update state
        if not args.dry_run:
            team['last_digest_ts'] = now.isoformat()
            team_file = team.pop('_file')
            atomic_write_json(team_file, team)
            log(f'[{team_name}] State updated: last_digest_ts={now.isoformat()}')
        elif '_file' in team:
            team.pop('_file')

    log('--- Team Digest run complete ---')


if __name__ == '__main__':
    main()
