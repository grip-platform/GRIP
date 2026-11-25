# MCP tunnel first contact

`bin/grip-tunnel` (backed by `lib/mcp_tunnel.py`, tested by
`tests/test_mcp_tunnel.py`) is the GRIP MCP tunnel kit: it builds and
checks a local Docker Compose deployment of Anthropic's MCP tunnel
stack, so cloud Claude (a Managed Agent session, or the Messages API)
can reach one MCP server running on this machine.

MCP tunnels are a **research preview** (vendor-docs/platform-overview.md,
read 2026-11-05): no uptime or support commitment, not eligible for zero
data retention, and Anthropic may change or discontinue the feature at any
time. Nothing in this kit changes that status; `grip-tunnel vendor-check`
watches for it to change (see below).

## What this kit does, and does not, wire

The kit scaffolds the vendor's two containers (`mcp-proxy`, `cloudflared`)
plus one container of its own: a nonce probe. **The nonce probe is the
only route this kit ever wires by default**
(`rules/untrusted-content-capability-lock.md`). It has exactly one MCP
tool, `first_contact(nonce)`, which signs a nonce with an Ed25519 key kept
in `data/`. Routing a real MCP server -- one with personal or client data
-- through the tunnel is the operator's own decision, made by hand-editing
`config/mcp-proxy.yaml`'s `routes` map; this kit never does it for you, and
the research-preview status above is exactly why: there is no zero-data-
retention cover for anything that crosses it.

## Prerequisites

- Docker and Docker Compose (`brew install colima docker docker-compose &&
  colima start` on a Mac with no other Docker runtime).
- OpenSSL 1.1.1+ on `PATH` (preinstalled on macOS).
- A Console role that can manage MCP tunnels
  (`platform.claude.com/settings/workspaces/default/mcp-tunnels`).

## First contact, step by step

1. **Create a tunnel in the Console.** New tunnel, leave **Set up
   programmatic access** off (this kit is the manual-credential-provisioning
   flow only -- no Workload Identity Federation, no spend-capable service
   account). Copy the **Domain** (`abcd1234.tunnel.anthropic.com`) and
   **Token**.

2. **Scaffold the deployment.**

   ```bash
   bin/grip-tunnel scaffold --dir ~/mcp-tunnel --domain abcd1234.tunnel.anthropic.com
   ```

   Writes `docker-compose.yaml`, `config/mcp-proxy.yaml` (one route: the
   probe), `mcp_tunnel_probe.py`, `probe-requirements.txt`, and generates the
   probe's own signing key under `data/`. Refuses to run if any of those
   files already exists, so it never overwrites or deletes a file it did not
   create. Under any `umask`, the proxy (UID `65532`) can still enter `data/`
   and read its config: `data/` gets search permission for other users, and
   no listing permission, and the config is mode 0644. The secrets inside
   `data/` stay 0600.

   `probe-requirements.txt` is a hash lock
   (`config/mcp-tunnel/probe-requirements.txt`): every Python package the
   probe runs, pinned by version and SHA-256. The probe installs it with
   `pip install --no-deps --require-hashes`, so a container restart
   installs the same bytes or refuses to start. The lock's first lines
   hold the command that rebuilds it.

3. **Generate the CA and server certificate.**

   ```bash
   bin/grip-tunnel certs --dir ~/mcp-tunnel
   ```

   A self-signed CA (`data/ca.crt`, `data/ca.key`, mode 0600, 10-year
   validity) and a server certificate (`data/tls.crt`, `data/tls.key`, mode
   0644, 90-day validity, SAN `*.<domain>`), meeting the vendor's
   certificate requirements (vendor-docs/platform-reference.md). `tls.key`
   is world-readable **on purpose**: the proxy container runs as the
   non-root UID `65532` and must read it through the bind mount; `ca.key`
   is the one that stays owner-only, because only this host ever reads it.

   Upload `data/ca.crt` to the tunnel in the Console (**Add certificate**).
   The **Needs certificate** badge clears once it is registered.

4. **Paste in the tunnel token.** The token is never written by this kit
   and never passed as a command-line argument. Paste it into a file
   yourself:

   ```bash
   printf '%s' 'PASTE_YOUR_TOKEN_HERE' > ~/mcp-tunnel/data/tunnel-token
   chmod 600 ~/mcp-tunnel/data/tunnel-token
   ```

5. **Check before you start.**

   ```bash
   bin/grip-tunnel doctor --dir ~/mcp-tunnel
   ```

   23 offline checks (below), exit 0 clean / 1 findings / 3 could not
   check. A clean deployment shows 21 of them, in 24 lines.
   `allowed-ips-type` and `allowed-ips-cidr` appear only when `allowed_ips`
   is malformed, and `key-size`, `cert-signature-hash` and `cert-days-left`
   print one line per certificate.

6. **Start it, then check the connection.**

   ```bash
   bin/grip-tunnel up --dir ~/mcp-tunnel
   bin/grip-tunnel status --dir ~/mcp-tunnel
   ```

   `status` counts the same two log lines the vendor's own quickstart
   verifies by: one `route configured` (the probe) and four `Registered
   tunnel connection` lines, and it confirms both containers are running
   now. `up`/`down`/`status`/`stop` are thin wrappers over `docker
   compose`; none of them does anything this kit's own tests don't already
   cover with a stub `docker` on `PATH` -- the real containers only run on
   the operator's own machine, by hand. A Docker CLI or daemon that hangs
   past 60 seconds gives a failed result, never a traceback, and a
   `TUNNEL_TOKEN` exported in your shell is never passed on: only
   `data/tunnel-token` reaches the containers.

   For a one-line reading at any time:

   ```bash
   bin/grip-tunnel weather --dir ~/mcp-tunnel
   # Tunnel up. Cert clear, 89 days. Proof: not of event.
   ```

   With `--seal-home <home>` it adds the record count of a
   `grasp-mcp-seal` home (`Seals 12.`). The count says what that host
   recorded, never that the record is complete, which is what "Proof: not
   of event" means.

7. **Prove first contact.**

   ```bash
   bin/grip-tunnel nonce new --dir ~/mcp-tunnel
   ```

   Prints a nonce and a sentence, and copies the sentence to the clipboard
   (`pbcopy`). In the Console, create a session (**Create new agent**),
   add this tunnel as an MCP server with **Subdomain** `probe` and **Path**
   `mcp` (resolves to `https://probe.<domain>/mcp`), and paste the sentence
   as the user turn. The probe's `first_contact` tool signs the nonce and
   returns the signature; copy that JSON reply.

   ```bash
   bin/grip-tunnel nonce verify --dir ~/mcp-tunnel --reply-file reply.json
   # or: paste the reply on stdin and omit --reply-file
   ```

   Paste the reply as it came: bare JSON, or JSON inside the session's
   prose and code fence. The kit takes the JSON object out of the text,
   and only the signature check decides whether it counts. Exit 0
   verified, 1 not verified (the reason is printed), 2 when the reply
   could not be read or holds no JSON, in one plain line. Ctrl-D or
   Ctrl-C at the `I saw it` prompt skips the ceremony and says so in one
   line: the first contact is still recorded in `first-contact.txt`, your
   confirmation is not, and the exit is 0.

   This proves to **you** that the path works end to end and that the
   reply is fresh (signed after the nonce was issued, within 10 minutes,
   single-use). It proves nothing to a stranger: the signing key never
   leaves `data/` on this machine.

   The probe source itself is tested end to end without a tunnel:
   `tests/test_mcp_tunnel.py` runs the generated script under the locked
   `mcp`, sends it the public `probe.<domain>` Host header, and verifies
   the signature it returns. The probe turns off the SDK's Host-header
   check, because the vendor does not document whether the proxy keeps
   that header, and no browser can reach the probe's loopback port.

8. **Stop it when you are done.**

   ```bash
   bin/grip-tunnel stop --dir ~/mcp-tunnel   # the brake: docker compose down --timeout 0
   ```

   `stop` rings the terminal bell and prints a card that says whether the
   stack is lowered, and the time. It never claims success on a failed
   teardown. Add `--seal-home <home>` to freeze the `grasp-mcp-seal`
   record count into the card.

   Delete the tunnel in the Console too, if the pilot does not earn its
   keep (`rules/*` nowhere requires keeping an unused tunnel registered).

## `doctor` -- the 23 checks

Each check has a stable rule id, so a finding can be grepped and tracked.
Unknown (could not check -- a missing file, usually) is never reported as
clean; its exit code (3) outranks a plain finding (1). A fourth status,
`not-checked`, is different again: it says plainly that nothing was verified
for this one check, without claiming `ok` and without raising the exit code
the way `unknown` does -- used where not checking is the normal case (a
hostname route other than `localhost`; see `allowed-ips-coverage` below),
not a failure to check.

| Rule id | What it checks |
|---|---|
| `routes-is-map` | `routes` is a YAML map, not a list (the vendor's proxy rejects a list at load). |
| `route-format` | Each route is `scheme://host:port`. |
| `route-port` | The port is present and numeric. |
| `route-no-path` | The route carries no path (the proxy rejects one at load). |
| `tunnel-domain-present` | `tunnel_domain` is set. |
| `upstream-is-map` | `upstream`, when present, is a map (`allowed_ips`, `disable_ip_validation`, `tls`), not a string or a list. For any other shape, doctor reports it and checks the routes against the proxy's defaults, instead of failing with an error. |
| `allowed-ips-coverage` | Every literal upstream IP falls inside the effective `allowed_ips`. `localhost` counts as a literal here: RFC 6761 reserves it for the loopback address, so the scaffolded probe route is checked as `127.0.0.1`, which is why `scaffold` adds `127.0.0.0/8` to `allowed_ips`. A CGNAT address (`100.64.0.10/10`, RFC 6598 -- Tailscale's own range among others) is named explicitly: it is **not** an RFC1918 private range, so the proxy's default does not cover it, even though it looks "private". Any other route given as a **hostname** (e.g. `my-upstream.internal`) resolves only at runtime, so there is nothing to check offline -- its status is `not-checked`, not `ok` (nothing was verified) and not `unknown` (that status's exit code, 3, would fire for every ordinary hostname route, which is the normal case, not a failure). `not-checked` leaves the exit code exactly where `ok` would. |
| `allowed-ips-exclusive` | `allowed_ips` and `disable_ip_validation` are not both set (the reference docs call them mutually exclusive). |
| `allowed-ips-type` | `allowed_ips` is a list, not a bare string (`"10.0.0.0/8"` instead of `["10.0.0.0/8"]` is the natural YAML typo) -- a non-list value is silently ignored and the RFC1918 default is used in its place. |
| `allowed-ips-cidr` | Every entry in `allowed_ips` is an IPv4 CIDR or address -- an entry that does not parse, or an IPv6 one, is named rather than silently dropped from coverage checking. The proxy takes IPv4 values only and connects upstream over IPv4 alone (vendor reference), so an IPv6 entry is never enforced. |
| `https-trust-anchor` | Any `https://` upstream route has `upstream.tls.ca_file` or `upstream.tls.include_system_cas` configured. When `routes` is not a map, no route can be read, so this says `not-checked` and leaves the finding to `routes-is-map`. |
| `cert-san` | The server certificate's SAN covers every route's hostname: either the wildcard `*.<tunnel-domain>`, which covers one label below the domain, or an exact name per route (`probe.<tunnel-domain>`). A SAN for the bare domain alone covers no route. |
| `cert-eku` | If the server certificate has an ExtendedKeyUsage extension, it includes `serverAuth` (the vendor's rule). No extension at all is `ok`. |
| `cert-signature-hash` | Both certificates are signed with SHA-256 or stronger (a digest of at least 32 bytes). SHA-1, SHA-224 and MD5 are findings. |
| `cert-aki-ski` | The server certificate's AuthorityKeyIdentifier matches the CA's SubjectKeyIdentifier -- a structural check on two unsigned extension VALUES, not a cryptographic proof of who signed it (see `cert-ca-signature`, next). |
| `cert-ca-signature` | The server certificate's signature cryptographically verifies against the CA's public key (`Certificate.verify_directly_issued_by`). `cert-aki-ski` alone can be fooled by a certificate whose AuthorityKeyIdentifier was copied from this CA but was actually signed by a different key; this is the real proof the vendor's own certificate requirements ask for ("signed directly by a registered CA"). |
| `ca-constraints` | The CA certificate has `CA:TRUE` (critical) and `keyCertSign`. |
| `tls-key-matches-cert` | `data/tls.key` exists, is a readable private key, and its public key matches the server certificate's. The generated proxy configuration requires this file to serve TLS; a missing, corrupt, or mismatched key is a finding, not a silent gap. |
| `key-size` | Both certificates are RSA >= 2048 or ECDSA P-256+. |
| `cert-days-left` | Both certificates are within their validity period: not valid only from a later date, not expired, and not within 30 days of expiry. |
| `key-world-readable` | `ca.key` is not readable by group or other. (`tls.key`'s PERMISSIONS are deliberately **not** checked here: the vendor's own quickstart requires `chmod 644` on it so the non-root proxy container can read it through the bind mount -- flagging the permission would be a false positive against the vendor's own documented step. `tls.key` itself is still checked, by `tls-key-matches-cert` above.) |
| `token-world-readable` | The tunnel token (`data/tunnel-token`), if present, is not readable by group or other. Not present yet (before the operator pastes the Console token in) is `ok`, not `unknown` -- there is nothing to check yet, not something uncheckable. |
| `probe-key-world-readable` | The probe's Ed25519 signing key (`data/probe-ed25519-private.key`) is not readable by group or other. `scaffold` always writes it at mode 0600; a looser mode here means something changed it afterwards. |

## Certificate renewal

`bin/grip-tunnel cert-renew --dir ~/mcp-tunnel` signs a fresh server
certificate from the SAME key and the stored CA -- no API call, no new CA
to re-register. openssl writes the new certificate beside the live one,
and it replaces `data/tls.crt` only after signing succeeds. A failed
renewal leaves the running certificate untouched (signing straight into
it used to leave it empty).

`bin/grip-tunnel cert-status --dir ~/mcp-tunnel` prints one line: `ok:`
and exit 0 with more than 30 days left; the warning and exit 1 within 30
days of expiry, after it, or when the certificate cannot be read; and
`not-checked:` with exit 3 when there is no certificate at all.

**This is also wired into the always-on tick** (`lib/grip_tick.py`,
`bin/grip-tick`, every 5 minutes): every directory `scaffold` has run
against is registered in
`$CLAUDE_DIR/state/mcp-tunnel/deployments.json`, and the tick reports once
when a registered deployment's certificate crosses the 30-day line or
cannot be read. A directory that no longer exists drops out of the list
the next time `scaffold` registers one. Nothing is registered, nothing is
reported, with no deployment scaffolded.

## Watching the vendor's own status

```bash
bin/grip-tunnel vendor-check
```

Fetches the vendor's overview page and compares `status` and
`zdr.eligibility` against the tracked baseline
(`config/mcp-tunnel/vendor-baseline.json`, last read 2026-11-05:
`research-preview` / `not-eligible`). Exit 0 unchanged, 1 changed, 3
unreachable.

The plan's reopen triggers (`plans/ULTRAPLAN-2026-11-05-mcp-tunnels.md`
section 8) name three conditions worth re-reading the plan over: tunnels
leaving research preview, gaining zero-data-retention cover, or reaching
claude.ai below Enterprise. This command mechanically watches **only the
first two** -- the vendor overview page's own frontmatter carries `status`
and `zdr.eligibility`, nothing about claude.ai's own product tiers. The
third stays a human's job: notice when Anthropic announces it.
`parse_vendor_frontmatter` returns exactly those two keys (`status`,
`zdr_eligibility`) and nothing else, so this scope claim is checkable
directly against the function it names.

## Security notes

- **The tunnel token and both private keys live only in `data/`,**
  mode-restricted, and are never written to a tracked path, never passed
  as a command-line argument, and never committed. Git-crypt is not the
  answer here -- they do not belong in any repository, encrypted or not
  (mirrors `rules/paramount-backups.md`'s posture on anything this
  sensitive).
- **If an attacker obtains the tunnel token AND a TLS private key, they
  can impersonate the proxy and read MCP payloads** (vendor-docs/platform-overview.md).
  Treat both as high-value secrets; see vendor-docs/platform-security.md
  for the full breach-response runbook (`stop` is its first step).
- **Everything that crosses the tunnel reaches Anthropic** under the
  standard research-preview retention policy. No personal or client data
  should go near it -- which is exactly why the only route this kit wires
  by default is the data-free nonce probe.
- **Rollback:** `bin/grip-tunnel stop --dir DIR`, archive the tunnel in the
  Console, `rm -rf DIR/data`.

## See also

- `skills/tunnel/SKILL.md` -- the `/tunnel` skill's MCP-tunnel section.
- `plans/ULTRAPLAN-2026-11-05-mcp-tunnels.md` -- the full plan this kit
  was built from (section 3 D3), including the evidence trail, the
  verdict on each GRIP-ecosystem system, and the deferred items.
- `vendor-docs/*.md` (session scratchpad) -- the vendor pages this kit's
  behaviour is grounded in: quickstart, concepts, reference, security,
  troubleshooting, deploy-compose, overview.
