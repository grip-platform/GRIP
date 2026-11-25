# VPS bootstrap runbook — grip-remote

One-file reference for rebuilding `grip-remote` (or any GRIP VPS) on Hetzner
Cloud. Two supported paths:

- **Path A — Snapshot restore** (preferred when migrating an existing
  server, e.g. moving to a bigger plan in a different datacenter because
  Rescale is out of stock locally).
- **Path B — Fresh Ubuntu 24.04** (preferred when the old server is
  unrecoverable, when you want a clean architecture, or when the
  config substrate has drifted enough that a rebuild is less work than
  a migration).

Architecture lock-in: an ARM64 (CAX) snapshot only restores onto a CAX
plan. To switch from ARM to x86 (CPX / CCX), use Path B.

## What `grip-remote` runs

For reference while planning a rebuild. Authoritative source is
`rules/operational-context.md`.

| Service stack | Process manager | Port | Notes |
|---------------|-----------------|------|-------|
| grip-server | pm2 | 3847 | Main GRIP server |
| grip-channel | pm2 | 3101 | WhatsApp/Slack/Discord bridge (may be STOPPED) |
| guild-dashboard | pm2 | 3849 | Guild metrics UI |
| tralala | pm2 | 9999 | Auxiliary service |
| pm2-logrotate | pm2 | — | Log rotation for the pm2 stack |
| grip-metrics | Docker | 9100 | Prometheus / node-exporter |
| grip-grafana | Docker | 3000 | Grafana dashboards |
| grip-gate | Docker | 9300 | Gate metrics sidecar |
| cloudflared | systemd | — | Outbound tunnel for `*.your-gateway.example.com` |
| tailscale | systemd | — | VPN layer; the only public-ish SSH path |

Public URLs are served via Cloudflare tunnel (cloudflared phones OUT),
so DNS never needs to change when the server moves — the tunnel
follows the `cloudflared` binary.

## Hetzner create-server choices (both paths)

| Section | Pick | Reason |
|---------|------|--------|
| **Image** | Snapshot (Path A) / Ubuntu 24.04 LTS (Path B) | Snapshot preserves all services; fresh is a clean-slate rebuild |
| **Type** | Cost-Optimized → Arm64 (Ampere®) | Matches current CAX architecture; right perf profile for Docker + pm2 + grafana |
| **Location** | Falkenstein (primary) / Nuremberg (fallback) | Healthy CAX41 stock in both; EU-central low latency |
| **Volumes** | Skip | 40 GB SSD fine for baseline; hot-attach later if Prometheus TSDB outgrows disk |
| **Firewalls** | `grip-remote-fw` (create first, see below) | Default-deny inbound reduces attack surface on Public IPv4 |
| **Backups** | Enable (+20% ≈ $7.40/mo on CAX41) | Daily rolling; complements manual snapshots |
| **Public IPv4** | Keep (~$0.73/mo) | SSH fallback + legacy IPv4 reach |
| **Public IPv6** | Keep (free) | Useful for cloudflared / outbound |
| **Private networks** | Skip | Single-node setup |
| **SSH keys** | `macbook-pro` + `grip-deploy` | Never "no key" — Hetzner emails root password in clear |
| **Cloud config** | See Path A or Path B YAML below | — |
| **Name** | `grip-remote-v2` | Avoid collision with running v1; rename after v1 is destroyed |

## Firewall — create BEFORE the server

Hetzner Console → left nav → **Firewalls** → Create firewall →
`grip-remote-fw`.

**Inbound rules:**

| Protocol | Port | Source | Purpose |
|----------|------|--------|---------|
| ICMP | — | `0.0.0.0/0`, `::/0` | Ping for monitoring |
| TCP | `22` | `0.0.0.0/0`, `::/0` | SSH (key-only + fail2ban) |

**Outbound:** leave default-allow. Tailscale, cloudflared, Docker image
pulls all phone out.

If you have a static admin IP, scope SSH to that CIDR instead of
`0.0.0.0/0` — tighter, and fail2ban becomes belt-and-braces rather than
primary.

## Path A — Snapshot restore cloud-init

The snapshot already carries Tailscale, cloudflared, pm2, Docker, and
fail2ban. The only action on a fresh machine is giving it a new
Tailscale identity so the old node can be decommissioned cleanly.

```yaml
#cloud-config
# Path A: Snapshot restore — minimal cloud-init.
# The snapshot already has all services configured.

runcmd:
  - [ tailscale, logout ]
  - tailscale up --authkey=REPLACE_WITH_TSKEY --hostname=grip-remote-v2 --ssh --accept-routes
  - systemctl restart cloudflared || true

timezone: UTC
```

Replace `REPLACE_WITH_TSKEY` with a reusable Tailscale auth key from
`https://login.tailscale.com/admin/settings/keys` (tick *Reusable*, set
tags to match old node).

## Path B — Fresh Ubuntu 24.04 cloud-init

Full platform bootstrap. Installs Tailscale, cloudflared (binary only),
Docker, Node 20 + pm2, fail2ban, unattended-upgrades, UFW, and tunes
sysctl for BBR + fq qdisc.

Design notes:

- **SSH drop-in lands before sshd restart** — the first SSH session
  (yours, after boot) hits the hardened config, not the default.
- **UFW is the LAST runcmd step** — services come up untouched, then
  the host firewall locks down.
- **Non-root `grip` user inherits root's SSH keys** via file copy —
  never embed keys in cloud-init (they end up in Hetzner metadata and
  image caches).
- **Cloudflared tunnel credential is NOT in cloud-init** — it is a
  secret identifying a specific tunnel. You `scp` it from the old
  server after first boot.

```yaml
#cloud-config
# Path B: grip-remote-v2 bootstrap — Ubuntu 24.04 LTS / Ampere ARM64 (CAX41)

timezone: UTC
ssh_pwauth: false

package_update: true
package_upgrade: true
packages:
  - curl
  - ca-certificates
  - gnupg
  - git
  - jq
  - ripgrep
  - fd-find
  - htop
  - tmux
  - ufw
  - fail2ban
  - unattended-upgrades
  - apt-transport-https

users:
  - name: grip
    gecos: GRIP operator
    groups: [sudo, docker]
    shell: /bin/bash
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    lock_passwd: true

write_files:
  - path: /etc/sysctl.d/99-grip-remote.conf
    permissions: "0644"
    content: |
      net.ipv4.tcp_congestion_control = bbr
      net.core.default_qdisc = fq
      net.netfilter.nf_conntrack_max = 262144
      net.core.rmem_max = 16777216
      net.core.wmem_max = 16777216

  - path: /etc/ssh/sshd_config.d/10-grip-harden.conf
    permissions: "0644"
    content: |
      PermitRootLogin prohibit-password
      PasswordAuthentication no
      KbdInteractiveAuthentication no
      PubkeyAuthentication yes
      X11Forwarding no
      MaxAuthTries 3
      LoginGraceTime 20s
      ClientAliveInterval 60
      ClientAliveCountMax 3

  - path: /etc/apt/apt.conf.d/20auto-upgrades
    permissions: "0644"
    content: |
      APT::Periodic::Update-Package-Lists "1";
      APT::Periodic::Unattended-Upgrade "1";
      APT::Periodic::AutocleanInterval "7";

  - path: /etc/apt/apt.conf.d/52unattended-upgrades-local
    permissions: "0644"
    content: |
      Unattended-Upgrade::Automatic-Reboot "true";
      Unattended-Upgrade::Automatic-Reboot-Time "03:30";
      Unattended-Upgrade::Remove-Unused-Dependencies "true";

  - path: /etc/fail2ban/jail.d/sshd.local
    permissions: "0644"
    content: |
      [sshd]
      enabled = true
      port = 22
      maxretry = 3
      bantime = 1h
      findtime = 10m

  - path: ~/.bashrc.d/aliases
    permissions: "0644"
    owner: "grip:grip"
    content: |
      alias fd=fdfind
      alias ll='ls -lh --color=auto'
      alias gst='git status'

runcmd:
  - sysctl --system
  - systemctl restart ssh

  - install -d -o grip -g grip -m 700 ~/.ssh
  - cp /root/.ssh/authorized_keys ~/.ssh/authorized_keys
  - chown grip:grip ~/.ssh/authorized_keys
  - chmod 600 ~/.ssh/authorized_keys

  - install -m 0755 -d /etc/apt/keyrings
  - curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  - chmod a+r /etc/apt/keyrings/docker.gpg
  - echo "deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu noble stable" > /etc/apt/sources.list.d/docker.list
  - apt-get update
  - DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  - systemctl enable --now docker

  - curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  - DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
  - npm install -g pm2
  - sudo -u grip bash -lc 'pm2 install pm2-logrotate && pm2 set pm2-logrotate:max_size 50M && pm2 set pm2-logrotate:retain 14'
  - env PATH=$PATH:/usr/bin pm2 startup systemd -u grip --hp ~

  - curl -fsSL https://tailscale.com/install.sh | sh
  - tailscale up --authkey=REPLACE_WITH_TSKEY --hostname=grip-remote-v2 --ssh --accept-routes

  - curl -fsSL -o /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
  - dpkg -i /tmp/cloudflared.deb
  - rm /tmp/cloudflared.deb

  - systemctl enable --now fail2ban

  - ufw --force reset
  - ufw default deny incoming
  - ufw default allow outgoing
  - ufw allow 22/tcp comment 'SSH'
  - ufw allow in on tailscale0 comment 'Tailscale LAN trust'
  - ufw --force enable

  - DEBIAN_FRONTEND=noninteractive apt-get autoremove -y
  - date -u +"grip-remote-v2 bootstrap complete at %FT%TZ" > /var/log/grip-bootstrap.log

final_message: "grip-remote-v2 bootstrap complete after $UPTIME seconds"
```

## Post-boot migration — Path B only

Cloud-init finishes in ~3 min. Then:

### 1. Verify the base platform

```bash
ssh grip@<new-ipv4>
sudo tail -30 /var/log/cloud-init-output.log     # confirm no red errors
sudo cat /var/log/grip-bootstrap.log              # step-10 marker
tailscale status | rg grip-remote-v2              # new node is up
docker --version && node --version && pm2 --version
ufw status verbose                                # 22/tcp + tailscale0 allowed
```

### 2. Upload the cloudflared tunnel credential

The credential JSON identifies a specific tunnel. It must be copied
from the old box or re-downloaded from the Cloudflare dashboard.

```bash
scp old-grip-remote:/etc/cloudflared/*.json grip-remote-v2:/tmp/
ssh grip@<new-ipv4>
sudo mv /tmp/*.json /etc/cloudflared/
sudo chown root:root /etc/cloudflared/*.json
sudo chmod 600 /etc/cloudflared/*.json

# Point cloudflared at the tunnel (if /etc/cloudflared/config.yml is not
# already in the creds dir, copy it over too)
scp old-grip-remote:/etc/cloudflared/config.yml grip-remote-v2:/tmp/config.yml
sudo mv /tmp/config.yml /etc/cloudflared/config.yml

sudo cloudflared service install
sudo systemctl enable --now cloudflared
curl -I https://your-gateway.example.com                  # 200 = tunnel live
```

### 3. Migrate pm2 apps

pm2's `dump.pm2` file is a JSON manifest of every managed process
(script path, args, env, restart policy). Copy the manifest + the
actual app directories.

```bash
# On old box: snapshot the pm2 state
ssh old-grip-remote 'pm2 save'
ssh old-grip-remote 'cat ~/.pm2/dump.pm2' > /tmp/pm2-dump.pm2

# Copy app directories (adjust to actual paths)
rsync -avz old-grip-remote:/srv/grip-server   grip-remote-v2:/srv/
rsync -avz old-grip-remote:/srv/grip-channel  grip-remote-v2:/srv/
rsync -avz old-grip-remote:/srv/guild-dashboard grip-remote-v2:/srv/
rsync -avz old-grip-remote:/srv/tralala       grip-remote-v2:/srv/

# On new box: restore pm2 state
scp /tmp/pm2-dump.pm2 grip-remote-v2:~/.pm2/dump.pm2
ssh grip@<new-ipv4> 'pm2 resurrect && pm2 save && pm2 list'
```

Verify each process shows `online` in `pm2 list` and the ports bind.

### 4. Migrate Docker stacks (grip-metrics, grip-grafana, grip-gate)

Compose files + their data volumes. Prometheus TSDB and Grafana SQLite
DB live in Docker volumes — lose them and you lose history and
dashboards.

```bash
# Stop Docker services on old box so volume data is consistent
ssh old-grip-remote 'cd /srv/docker/grip-metrics && docker compose stop'
ssh old-grip-remote 'cd /srv/docker/grip-grafana && docker compose stop'
ssh old-grip-remote 'cd /srv/docker/grip-gate   && docker compose stop'

# Copy compose directories
rsync -avz old-grip-remote:/srv/docker/ grip-remote-v2:/srv/docker/

# Copy Docker-managed volumes (see §5 for Prometheus/Grafana detail)
ssh old-grip-remote 'docker run --rm -v prometheus_data:/src -v /tmp:/dst alpine \
  tar czf /dst/prom_data.tgz -C /src .'
scp old-grip-remote:/tmp/prom_data.tgz grip-remote-v2:/tmp/

ssh old-grip-remote 'docker run --rm -v grafana_data:/src -v /tmp:/dst alpine \
  tar czf /dst/grafana_data.tgz -C /src .'
scp old-grip-remote:/tmp/grafana_data.tgz grip-remote-v2:/tmp/

# On new box: restore volumes before starting compose stacks
ssh grip@<new-ipv4>
sudo docker volume create prometheus_data
sudo docker run --rm -v prometheus_data:/dst -v /tmp:/src alpine \
  tar xzf /src/prom_data.tgz -C /dst
sudo docker volume create grafana_data
sudo docker run --rm -v grafana_data:/dst -v /tmp:/src alpine \
  tar xzf /src/grafana_data.tgz -C /dst

# Start stacks
cd /srv/docker/grip-metrics  && sudo docker compose up -d
cd /srv/docker/grip-grafana  && sudo docker compose up -d
cd /srv/docker/grip-gate     && sudo docker compose up -d

# Verify
curl -s http://localhost:3000/api/health          # Grafana
curl -s http://localhost:9100/metrics | head -3   # node-exporter
curl -s http://localhost:9300/metrics | head -3   # grip-gate
```

### 5. Prometheus + Grafana volume gotchas

| Symptom | Cause | Fix |
|---------|-------|-----|
| Grafana login prompt doesn't accept old password | SQLite DB not restored | Re-run the `docker run --rm -v grafana_data … tar xzf` step; check `docker volume ls` |
| Prometheus `/-/ready` returns 503 for minutes | TSDB doing WAL replay on ~GB of data | Wait; check `docker logs` — "Replaying WAL, this may take a while" |
| Prometheus reports "opening storage failed" | Volume perms wrong after tar extract | `docker run --rm -v prometheus_data:/data alpine chown -R 65534:65534 /data` |
| Grafana dashboards missing after restore | Dashboards provisioned via file, not DB | Copy `/etc/grafana/provisioning/` tree too (usually in compose bind mount) |
| `cadvisor` / `node_exporter` targets DOWN on new box | Compose expected old hostnames | Update `prometheus.yml` scrape_configs with new resolvable names |

### 6. Cutover — DNS, Tailscale, GRIP config

```bash
# Verify parallel state — both boxes up, both tunnels green, new box serving
curl -I https://your-gateway.example.com                  # which node answers? check headers
ssh grip@<new-ipv4> 'sudo journalctl -u cloudflared --since "10 min ago" | rg ingress'

# On Tailscale admin (https://login.tailscale.com/admin/machines):
#   - New node (grip-remote-v2) should be Active
#   - Old node can be kept for 24h as rollback, then expired/removed

# Update GRIP operational context
# rules/operational-context.md → change 100.64.0.10 to new Tailscale IP
# Check with: tailscale ip -4 on new box
```

### 7. Update everything that hard-codes the old Tailscale IP

Search the GRIP repo for `100.64.0.10`:

```bash
rg -n '100\.66\.66\.92' ~/.claude
```

Expected hits (from rules/operational-context.md):
- `rules/operational-context.md` itself
- Any pair-mode docs that reference grip-remote
- Possibly `bin/grip-*` wrappers that SSH into grip-remote
- Andre/Toopie pair-mode configuration files

Replace with the new IP (or better — replace with `grip-remote`
Tailscale MagicDNS name so future moves don't break anything).

### 8. Destroy the old server

After 24h of parallel running with the new box healthy:

1. Hetzner Console → `grip-remote` → Delete server
2. Keep the original snapshot for ~7 days as a rollback anchor, then
   delete it
3. Tailscale admin → remove the old node entry
4. If using Hetzner Backups, the old box's backups expire automatically

Rename new server: Hetzner Console → `grip-remote-v2` → rename to
`grip-remote`.

## Rollback

If the new box goes sideways within 24h:

1. Stop cloudflared on new box: `sudo systemctl stop cloudflared`
2. Start cloudflared on old box: `sudo systemctl start cloudflared`
   — the tunnel phones back out, public URLs come back through the
   old node
3. Point GRIP operational context back at old Tailscale IP
4. Investigate new box offline; retry cutover after fix

If rollback is needed AFTER 24h (data-loss window ≈ 24h of fresh
traffic): restore from the original snapshot at Hetzner and re-run
cutover in reverse.

## Gotchas reference

| Gotcha | Why it happens | Mitigation |
|--------|----------------|------------|
| First `ssh grip@...` warns "host key changed" | New machine, new host keys | `ssh-keygen -R <old-ip>` before connecting |
| `fd` command not found | Ubuntu ships `fdfind` | Alias in `~/.bashrc.d/aliases` (already written by cloud-init) |
| `pm2` systemd unit doesn't restart apps on reboot | Ran `pm2 startup` but never `pm2 save` | Always `pm2 save` after `pm2 resurrect` |
| cloudflared 502 "unable to reach origin" | Origin service (grip-server) not bound yet | Start pm2 apps BEFORE installing cloudflared service |
| Grafana dashboards empty | Dashboard JSON is provisioned via compose bind mount, not volume | Include `/srv/docker/grip-grafana/provisioning/` in rsync |
| Prometheus losing 5-15 min of data during migration | Old box still scraping after stop, new box not yet scraping | Acceptable; scrape gap is visible as a gap in Grafana |
| Tailscale node appears as `grip-remote-v2` not `grip-remote` | Hostname flag in cloud-init | Rename in Tailscale admin after v1 decommission |
| Hetzner firewall attached but SSH still blocked | Rules not saved, or rules only on IPv6 | Check `Firewalls` page — rules must cover both `0.0.0.0/0` AND `::/0` |
| UFW allow tailscale0 applies before tailscaled creates the iface | Race condition on first boot | Harmless — UFW re-applies on iface up-event |
| fd-find / ripgrep not found on shell startup | `$PATH` lookup in `runcmd` runs as root, not grip | Aliases in `~/.bashrc.d/` cover this; check `.bashrc` sources that dir |

## Reference links

- Hetzner Cloud docs: https://docs.hetzner.com/cloud/
- Cloud-init modules: https://cloudinit.readthedocs.io/en/latest/topics/modules.html
- Tailscale auth keys: https://login.tailscale.com/admin/settings/keys
- Cloudflared ARM64 release: https://github.com/cloudflare/cloudflared/releases/latest
- Docker on Ubuntu: https://docs.docker.com/engine/install/ubuntu/
- NodeSource for Node 20: https://github.com/nodesource/distributions

## Maintenance

Update this runbook when:

- Ubuntu LTS changes (24.04 → 26.04) — adjust codename `noble`
- Node LTS changes — adjust `setup_20.x` to the current LTS
- Hetzner adds a new ARM plan above CAX41 — update the plan table
- Cloudflared changes install path or service command
- `grip-remote` acquires a new service — add it to the stack table

Owner: L>> (V>>)
Last updated on first write — grep `git log docs/vps-bootstrap.md` for history.
