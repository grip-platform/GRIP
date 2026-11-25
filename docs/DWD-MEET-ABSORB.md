# Domain-Wide Delegation for Meet Absorb

Scaffold for org-wide Google Meet Gemini Notes absorption across every
`codetonight.co.za` Workspace user without per-user OAuth.

## Why DWD

The existing `scripts/meet-absorb.sh` pipeline relies on the `gog` CLI, which
only authenticates as the user running the CLI (user-OAuth). Workspace admin
user-OAuth accounts still respect per-file ACLs, so they cannot see another
user's private My Drive contents. This was proven empirically:

> Both `licensing@codetonight.co.za` and `development@codetonight.co.za` (each a
> Workspace admin account) were authenticated via `gog` and queried for files
> modified today. Both returned **0 files** when the meeting organiser was
> `development@codetonight.co.za`. Gemini Notes docs live in the organiser's private
> My Drive until post-meeting auto-share, so admin user-OAuth cannot reach them.

Domain-Wide Delegation (DWD) solves this by letting a service account
**impersonate any user in the domain** for a pre-approved set of scopes. The
service account signs a JWT with `sub=development@codetonight.co.za`, Google
issues an access token scoped to that user, and Drive/Docs/Calendar calls
behave as if the target user made them.

## One-time setup (human hands required)

### 1. GCP Console

1. Go to <https://console.cloud.google.com/>.
2. Create (or reuse) a project, e.g. `grip-meet-absorb`.
3. Enable APIs:
   - Google Drive API
   - Google Docs API
   - Google Calendar API
4. Create a service account:
   - IAM & Admin > Service Accounts > Create service account
   - Name: `grip-meet-absorb-sa`
   - Skip role grants (DWD does not need project-level roles).
5. Enable DWD on the service account:
   - Click the service account > Details > Advanced settings
   - Tick "Enable Google Workspace domain-wide delegation"
   - Set an OAuth consent screen product name (required).
   - Save.
6. Create and download a JSON key:
   - Keys > Add key > Create new key > JSON > Create
   - Save as `~/.claude/.secrets/dwd-key.json` (do not commit).
7. Copy the service account **Client ID** (the numeric `oauth2ClientId`
   shown in the Details tab). You will need it in the next step.

### 2. admin.google.com (Workspace super admin)

1. Go to <https://admin.google.com/> as a Workspace super admin
   (`licensing@codetonight.co.za` or `development@codetonight.co.za`).
2. Security > Access and data control > API controls.
3. Manage Domain Wide Delegation > Add new.
4. Paste the service account Client ID from step 1.7.
5. Paste the required OAuth scopes (comma-separated, no spaces). The canonical
   list lives in `lib/meet_absorb_dwd.py` as the `SCOPES` tuple — copy from
   there to keep the two in sync. ALL SCOPES mode includes 34 scopes covering
   Drive, Docs, Sheets, Slides, Forms, Calendar, Gmail, Admin Directory,
   Admin Reports, Chat, Tasks, Keep, Meet, People, Contacts, and Classroom.

6. Authorise.

Changes propagate within a few minutes. Rationale: ALL SCOPES grant the
service account full read+write access to Workspace services for any
impersonated user. The `admin.reports.*` scopes require the target admin
user to hold the **Admin Auditor** role in admin.google.com.

## Required scopes (ALL SCOPES mode)

| Service | Scopes | Purpose |
|---------|--------|---------|
| Drive | `drive`, `drive.file`, `drive.metadata`, `drive.appdata` | Full read/write access to all files in target user's Drive |
| Docs/Sheets/Slides/Forms | `documents`, `spreadsheets`, `presentations`, `forms.body`, `forms.responses.readonly` | Create, read, and modify Workspace documents |
| Calendar | `calendar`, `calendar.events` | Read and modify calendar events |
| Gmail | `https://mail.google.com/`, `gmail.modify`, `gmail.send`, `gmail.settings.basic` | Full mailbox access, send, and settings management |
| Admin Directory | `admin.directory.user[.readonly]`, `admin.directory.group[.readonly]`, `admin.directory.orgunit`, `admin.directory.domain.readonly` | Dynamic user/group/orgunit enumeration for `--auto` mode |
| Admin Reports | `admin.reports.audit.readonly`, `admin.reports.usage.readonly` | Audit and usage reports (admin auditor role required) |
| Chat | `chat.messages`, `chat.spaces` | Read and send Google Chat messages |
| Tasks / Keep / Meet | `tasks`, `keep`, `meetings.space.created`, `meetings.space.readonly` | Task lists, Keep notes, Meet conference spaces |
| People / Contacts | `contacts`, `contacts.other.readonly`, `directory.readonly` | Contact and people directory access |
| Classroom | `classroom.courses` | Classroom course data |

**ALL SCOPES rationale**: V>> directive during Gen 509 RSI sprint — ALL
scopes required to enable GRIP to reason over the full Workspace surface
(Drive + Gmail + Calendar + Chat + Meet + Admin Directory), not just meeting
absorb. Reducing scopes later requires re-running the admin.google.com
delegation step, so provisioning the full set up-front avoids repeated
admin intervention.

## Security notes

- **Key storage**: `~/.claude/.secrets/dwd-key.json` only. The `.secrets/`
  directory is gitignored (see `.gitignore` section 1). Never commit.
- **Impersonation audit**: Every impersonation call is logged by Google in the
  Workspace Admin audit log under "Token". Reviewable by admins.
- **Scope bounding**: ALL SCOPES mode grants full read/write across the
  Workspace suite for every impersonated user. This is an intentional
  trade-off chosen by V>> to avoid repeated admin.google.com re-delegation.
- **Key rotation**: Rotate the JSON key annually. Create a new key, update
  `~/.claude/.secrets/dwd-key.json`, then delete the old key in GCP Console.
- **Cross-org gate**: `scripts/meet-absorb-dwd.py` refuses to absorb a meeting
  where attendees include non-`@codetonight.co.za` addresses unless
  `--allow-cross-org` is passed. Prevents accidental absorption of third-party
  meeting content.

## Dynamic user enumeration (`--auto`)

`scripts/meet-absorb-dwd.py --auto` no longer reads a static
`config/dwd-users.json` file. It now calls the Admin SDK Directory API via
`lib.meet_absorb_dwd.list_domain_users(admin_user)` to enumerate every
active (not suspended, not archived) user in the domain in real time. The
admin user defaults to `$DWD_ADMIN_USER` or `licensing@codetonight.co.za` and
can be overridden with `--admin-user`:

```bash
# Enumerate domain via laurie@, scan every user's live meetings
scripts/meet-absorb-dwd.py --auto

# Override admin user for the Directory lookup
scripts/meet-absorb-dwd.py --auto --admin-user development@codetonight.co.za
```

The admin user must hold Directory API read privileges in admin.google.com.

## Rollback

To disable DWD:

1. admin.google.com > Security > API controls > Manage Domain Wide Delegation
2. Find the service account row > Remove.

To revoke the key (without touching delegation):

1. GCP Console > IAM & Admin > Service Accounts > grip-meet-absorb-sa > Keys
2. Delete the key.
3. Remove `~/.claude/.secrets/dwd-key.json` locally.

Either action immediately breaks the DWD path. The legacy `gog`-based
`meet-absorb.sh` remains functional for user-OAuth scenarios.
