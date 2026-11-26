---
name: share-your-grip
description: Guided onboarding wizard for the GRIP mesh service. Walks through pre-flight checks, starting grip-server, configuring consent, optional tunnel exposure, adding peers, and verifying the mesh. Use when the user wants to share their GRIP knowledge base with peers or join the mesh network.
status: Active
effort: medium
version: 1.0.0
triggers:
  - "/share-your-grip"
  - "share my GRIP"
  - "connect to mesh"
  - "join mesh"
  - "mesh service"
---

# /share-your-grip -- GRIP Mesh Onboarding Wizard

Guided wizard to bring your GRIP instance online as a mesh node, allowing peers to query your KONO knowledge base.

## Usage

```
/share-your-grip     # Run the full onboarding wizard
```

## Protocol

### Step 1: Pre-flight Checks

Verify the three prerequisites before starting anything.

**KONO health:**

```bash
cd ~/.claude && venv/bin/python3 -c "from embeddings import EmbeddingEngine; e = EmbeddingEngine(); print(e.count()); e.close()"
```

Expected output: a positive integer (embedding count). If it errors, KONO is not healthy -- stop and resolve before continuing.

**Genome exists:**

```bash
ls ~/.claude/data/genomes/alex.json
```

Expected: file present and readable. If missing, the mesh has nothing to offer on genome queries.

**grip-server cloned:**

```bash
ls ~/CodeTonight/grip-server/
```

Expected: directory present with `server.js` visible. If missing:

```bash
git clone git@github.com:CodeTonight-SA/grip-server.git ~/CodeTonight/grip-server
```

If all three pass, proceed to Step 2. If any fail, halt and fix before continuing.

---

### Step 2: Start grip-server

```bash
cd ~/CodeTonight/grip-server && PORT=3099 node server.js >> /tmp/grip-server.log 2>&1 &
echo "grip-server PID: $!"
```

Allow 2-3 seconds for the server to bind, then verify:

```bash
curl -s http://localhost:3099/api/mesh/health
```

Expected: JSON with `{ "status": "ok" }` or similar healthy response. If the curl fails, check `/tmp/grip-server.log` for startup errors.

---

### Step 3: Configure Consent

Show the current consent configuration:

```bash
cat ~/.claude/mesh-consent.yaml 2>/dev/null || echo "(no consent file found -- will create)"
```

**AskUserQuestion**: Which KONO types should be shared with peers?

Options (select one or more, default: concept_skill, procedural, insight):
- `concept_skill` -- conceptual knowledge and skills
- `procedural` -- step-by-step procedures
- `insight` -- learned insights and heuristics
- `all` -- share all KONO types
- `none` -- query-only mode (receive from peers, share nothing)

**AskUserQuestion**: Share genome data with peers? (default: no)

Genome data includes personal attributes from `data/genomes/alex.json`. Only enable if you trust all peers explicitly.

Write the consent file based on answers:

```bash
cat > ~/.claude/mesh-consent.yaml << 'EOF'
# GRIP Mesh Consent Configuration
# Edit manually or re-run /share-your-grip to update

share:
  kono_types:
    - concept_skill
    - procedural
    - insight
  genome: false

peers: []
EOF
```

Update the `kono_types` list and `genome` flag to match the user's choices before writing.

---

### Step 4: Expose via Tunnel (Optional)

**AskUserQuestion**: Expose grip-server to the internet via cloudflared tunnel? (default: no -- Tailscale only)

**If no (Tailscale only):**

Show the Tailscale IP so peers can connect directly over the VPN:

```bash
tailscale ip -4 2>/dev/null || ifconfig | grep '100\.' | awk '{print $(2)}'
```

Peer connection URL will be: `http://<tailscale-ip>:3099`

Share this URL with peers who are on the same Tailscale network.

**If yes (cloudflared tunnel):**

Run `/tunnel 3099` -- this invokes the tunnel skill against port 3099 and returns a public `*.trycloudflare.com` URL. Note that cloudflared URLs are ephemeral and change on restart.

---

### Step 5: Add Peers

**AskUserQuestion**: Enter peer URLs to connect to (comma-separated), or press Enter to skip.

Example input: `http://100.64.0.10:3099, https://abc123.trycloudflare.com`

For each peer URL provided, register it with the local mesh:

```bash
# For each peer URL, derive the peer name from the hostname
PEER_URL="<url>"
PEER_NAME=$(echo "$PEER_URL" | sed 's|https\?://||' | sed 's|[:./].*||')

curl -s -X POST http://localhost:3099/api/mesh/peers \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"$PEER_NAME\", \"url\": \"$PEER_URL\"}"
```

Report the result of each registration (success or error) before moving to the next peer.

---

### Step 6: Verify Mesh

Run a test query across all connected peers:

```bash
curl -s "http://localhost:3099/api/mesh/query-all?q=test"
```

Report a summary:

| Item | Status |
|------|--------|
| Peers connected | (count from response) |
| Query succeeded | yes / no |
| KONO types offered | (from consent file) |
| Genome sharing | enabled / disabled |
| Exposure | Tailscale / cloudflared at \<url\> |

If any peer returns an error, show the peer name and error so the user can investigate.

---

## Stopping the Mesh Node

To stop grip-server cleanly:

```bash
pkill -f "node server.js" && echo "grip-server stopped"
```

To stop the cloudflared tunnel (if running):

```bash
pkill -f "cloudflared.*tunnel" && echo "tunnel stopped"
```

## Logs

| Component | Log location |
|-----------|-------------|
| grip-server | `/tmp/grip-server.log` |
| cloudflared | `/tmp/cloudflared-grip.log` |

## Consent File Location

`~/.claude/mesh-consent.yaml` -- edit directly to update sharing preferences without re-running the wizard.
