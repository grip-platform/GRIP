---
name: api-reverse-engineering
description: Systematically reverse engineer authenticated web APIs from browser DevTools captures. Use when user provides network request details or invokes /reverse-api. Legal when using own credentials.
status: Active
effort: high
version: 1.0.0
triggers:
  - /reverse-api
  - "reverse engineer API"
  - network request details
---

# API Reverse Engineering Skill

**Purpose:** Automate the process of reverse engineering web APIs from network captures to production-ready client code with validation and monitoring.

**Activation:** When user provides network request details, mentions "reverse engineer API", or invokes `/reverse-api`

**Legal Requirement:** ⚖️ **MUST have legitimate credentials and authorized access**. Refuse if unauthorized.

---

## When to Use This Skill

✅ **Use when**:

- User has account credentials for the platform
- Extracting own data from poorly-documented APIs
- Building automation for manual workflows
- Platform has no official API docs
- Need to monitor for API changes
- Research, or demonstrating a verification tool (for example, showing GRASP produces a sealed, replayable record of an API capture) against your own authenticated access

❌ **DO NOT use when**:

- No legitimate credentials
- Scraping public data without auth
- Violating platform ToS
- User requests "hacking" or unauthorized access

---

## Discovery Protocol (7 Steps)

### Step 1: Legal & Ethical Gate

### Required confirmation

```bash
Q: Do you have legitimate account credentials for this platform?
Q: Are you extracting your own data (not scraping others)?
Q: Have you reviewed the platform's Terms of Service?
```

If any answer is "no" or uncertain: **STOP and explain legal risks**.

### Step 2: Auth Mechanism Detection

### Ask user
```bash
Paste the login request from DevTools Network tab:
- Method (POST/GET)
- URL
- Request body
- Response headers (look for Set-Cookie, Authorization)
```

### Identify pattern
- **Session-based**: `Set-Cookie: PHPSESSID=...` or `JSESSIONID=...`
- **Bearer token**: `Authorization: Bearer xxx` in subsequent requests
- **Basic auth**: `Authorization: Basic xxx` (rare for modern apps)
- **OAuth**: Redirect flow (more complex, may need manual token)

### Step 3: Endpoint Pattern Analysis

### Ask user
```text
Paste export/data request:
- URL (e.g., /includes/exportdata.php)
- Method
- Request body (form data or JSON)
- Response headers (Content-Disposition, Content-Type)
```

### Detect pattern
- **Single endpoint, multiple params**: Form keys discriminate entities (NannyLogic style)
- **RESTful**: `/api/v1/users`, `/api/v1/jobs` (standard REST)
- **GraphQL**: Single `/graphql` endpoint with query payload
- **RPC**: `/rpc/exportData?type=users`

### Step 4: Entity Mapping (if multi-entity)

**For form-based APIs** (like NannyLogic):
```text
Q: How many different data types/entities can you export?
Q: For each, paste the POST body when clicking that export button.

Example:
- Candidates: comp_id=187&appexport=Export
- Jobs: comp_id=187&jobsexport=Export
- Clients: comp_id=187&clientexport=Export
```

Build JSON mapping:
```json
{
  "entities": [
    {"name": "Candidates", "form_key": "appexport", "filename": "Candidates.csv"},
    {"name": "Jobs", "form_key": "jobsexport", "filename": "Jobs.csv"}
  ]
}
```

### Step 5: Response Validation

### Extract from first download
```python
# Get column names from CSV header
# Store as expected schema
# Detect encoding (UTF-8, Latin1, etc.)
```

Create `expected_schemas.json`:
```json
{
  "Candidates.csv": {
    "key_columns": ["id", "name", "email"],
    "min_rows": 1000,
    "encoding": "utf-8"
  }
}
```

### Step 6: Modular Client Generation

**Write these modules directly, one responsibility each** (this skill ships no
template files; the shapes below are the contract):

1. **lib/{platform}_auth.py** - Authentication only (SRP)
   - login() → session_id
   - is_valid() → bool
   - Shape: a session-cookie login or a bearer-token login, chosen in Step 2

2. **lib/{platform}_client.py** - API calls only (SRP)
   - download_entity() → Path
   - download_all() → dict[str, Path]
   - Shape: takes the auth object; one method per entity pattern from Step 3

3. **scripts/download_{platform}.py** - Orchestration
   - Loads .env credentials
   - Authenticates
   - Downloads with retries
   - Logs results
   - Sends notifications

4. **scripts/validate_{platform}_api.py** - Daily health check
   - Schema validation
   - Row count monitoring
   - File size checks
   - Alerts on failures

### Step 7: Risk Documentation

### Generate confidential assessment
```markdown
# {PLATFORM} Technical Risk Assessment

## What We Found
- [Technical debt indicators from API design]
- [Vendor lock-in risks]
- [Data extraction challenges]

## Mitigation Strategy
- Automated exports (twice daily)
- Validation monitoring (daily checks)
- Cache fallback (12-hour TTL)

## Exit Strategy
- Month 1-2: Use automation, keep paying vendor
- Month 3-4: Build own data entry
- Month 5-6: Migrate historical data
- Month 7+: Cancel vendor subscription
```

---

## Parameters every generated module carries

Earlier versions of this skill promised a directory of template files; it never
existed in GRIP (removed from the text 2026-10-10 rather than invented after
the fact). The parameters are still the same five, written into the generated
modules directly:

- `PLATFORM` - Platform name
- `BASE_URL` - API base URL
- `AUTH_TYPE` - session, bearer, basic
- `COMPANY_ID` - If multi-tenant
- `ENTITIES` - List of data types

---

## Token Efficiency Rules

✅ **Reuse the reference implementation** - Copy its structure, change the parameters
✅ **Reference existing code** - "See the client repo's auth module for the shape"
✅ **Interactive prompts** - Guided questions, not walls of text
✅ **Batch operations** - Create all files in one TodoWrite cycle

❌ **Don't explain patterns repeatedly** - The reference implementation encodes them
❌ **Don't regenerate same structure** - Copy-paste with parameter substitution
❌ **Don't write long documentation** - Link to existing examples

---

## Example: NannyLogic Session (the client repository, not GRIP)

### Reference implementation (lives in that client's own repository)
- Auth: `lib/nannylogic_auth.py` (session-based, PHPSESSID)
- Client: `lib/nannylogic_downloader.py` (form POST with entity-specific keys)
- Mapping: `config/entity_mapping.json` (12 entities, unique form_key per entity)
- Validation: `scripts/validate_export_api.py` (schema + row count checks)
- Assessment: `docs/CONFIDENTIAL_TECHNICAL_ASSESSMENT.md`

**When creating new API client**: Copy structure, replace parameters, adapt auth mechanism.

## Beyond web APIs: binaries, apps and bundles

This skill stops at the HTTP boundary. For a shipped binary, an Electron or
JavaScript bundle, an APK, firmware or a captured runtime, use
`/reverse-engineering` (`skills/reverse-engineering/SKILL.md`), which drives
the REA tool server, attached opt-in per launch (never in the default
`.mcp.json`), and carries the same authorisation gate.

---

## Slash Command Integration

**Command**: `/reverse-api`

### Invocation
```bash
/reverse-api --platform Salesforce --auth-type bearer
```

### Flow
1. Legal/ethical gate checks
2. Interactive questionnaire (7-step protocol)
3. Generate the auth, client, orchestration and validation modules
4. Test authentication
5. Download sample data
6. Create validation system
7. Output risk assessment

---

## Common Patterns

### Pattern 1: Form POST with Session Auth (NannyLogic style)
```python
# Multiple entities, single endpoint
# Different form keys per entity
# Session cookie for auth
```
**Shape**: session-cookie auth module + form-POST client module

### Pattern 2: RESTful with Bearer Token
```python
# Multiple endpoints: /api/v1/users, /api/v1/jobs
# Authorization: Bearer {token} header
# JSON responses
```
**Shape**: bearer-token auth module + REST client module

### Pattern 3: GraphQL
```python
# Single endpoint: /graphql
# POST with query/mutation in body
# Bearer or session auth
```
**Shape**: bearer-token auth module + GraphQL client module

---

## Related Skills

- **gitignore-auto-setup**: Auto-exclude .env files with credentials
- **claude-code-agentic**: Use for multi-step execution with verification gates
- **self-improvement-engine**: Meta-skill that created this skill

---

## Legal Disclaimer

### This skill is for legitimate data extraction only:
- ✅ Using your own account credentials
- ✅ Extracting your own data
- ✅ Automating manual workflows you're already authorized to perform
- ❌ NOT for scraping others' data
- ❌ NOT for bypassing access controls
- ❌ NOT for violating Terms of Service

**When in doubt**: Consult platform ToS or legal counsel.

---

## Success Metrics

### Skill activation successful if
- Generated client authenticates on first try
- Downloads data without errors
- Validation system detects schema correctly
- Risk assessment provides actionable recommendations
- Total token usage <15k for complete implementation

### Quality indicators
- Code follows SOLID principles (separate auth, client, orchestration)
- Reuses the reference shapes (not regenerating patterns)
- Includes error handling and retries
- Notifications on failures
- Confidential assessment is business-focused (not technical jargon)
