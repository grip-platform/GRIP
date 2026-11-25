# Connect a Windows PC to the GRIP team session mesh

**Who this is for:** A>> (Andre) and F>> (Fabio), on Windows. V>> can also run
these steps for you live, or drive your machine over SSH.

**What the mesh does, in one sentence:** it lets every teammate's GRIP session
see what the others are doing in real time — so when one of us learns something
or ships something, the others' sessions know about it too. Three machines join
one shared "session mesh": V>> (blackbook), A>> (desktop-6kg0vq4), and F>>
(fabio-desktop), all over the private Tailscale network.

You only have to do this once per machine. Re-running the steps is always safe —
if something is already done, the script just says "ok" and moves on.

---

## The three moving parts (so the words below make sense)

Each machine runs three small background pieces. You do not start them by
hand — the join script does:

- **Bus** — a local hub on your PC (port 9474) that messages flow through.
- **Relay** — the bridge (port 9475) that carries messages to and from the
  other two machines over Tailscale.
- **Client** — the piece that speaks for your GRIP session on the mesh.

There is one shared password (a "token") that all three machines use so they
only talk to each other and nobody else.

---

## Before you start (prerequisites)

1. **GRIP is already installed** on your PC from the Windows onboarding
   (`scripts/grip-windows-onboard.sh` — the induction step). If not, do that
   first: see `docs/windows-onboarding-fabio-andre.md`.
2. **Tailscale is installed and signed in** to the `operator.github`
   network. Check by opening Git-Bash and running `tailscale ip -4` — it should
   print an address that starts with `100.`.
3. **Node.js is installed.** Check with `node --version`.
4. **Python with the `websockets` add-on.** The join script will try to install
   `websockets` for you automatically; if it cannot, it prints the one command
   to run.

Everything below is typed into **Git-Bash** (the terminal that came with Git for
Windows), not PowerShell.

---

## Step 0 — Make sure `.claude` points at `.grip` (one-time, in sync)

GRIP lives in `~/.grip`, but Claude Code reads `~/.claude`. They **must be the
same folder**, or a fresh Claude Code session won't see GRIP. The Windows
onboarding sets this up, but confirm (or fix) it directly — safe to run anytime:

```bash
bash ~/.grip/bin/grip-harness-link
```

- If it says *"already resolves to ~/.grip"* — you are in sync, continue.
- If it **refuses** because your `~/.claude` is a *real* folder, run the exact
  `--force-backup` line it prints. That safely moves your Claude login into
  GRIP, backs up the rest to `~/.claude.bak-<timestamp>`, and creates the
  pointer. **Nothing is lost** — you will not have to sign in to Claude again.

The mesh join script also runs this check for you, so you are covered either
way. Full explanation: `docs/windows-onboarding-fabio-andre.md` §4.

---

## Step 1 — Get the shared token from V>> (the one manual step)

The token is a shared secret. It is **never** stored in GRIP and never sent in a
GitHub repo. V>> hands it to you directly.

- **On V>>'s Mac**, V>> runs:

  ```bash
  cat ~/.grip-session-mesh/token
  ```

  That prints one line of letters and numbers.

- **V>> sends you that line** over a secure channel (a direct WhatsApp/Slack
  message is fine). Keep it private — treat it like a password, because it is
  one.

You do **not** invent your own token, and the script will **not** generate one.
Everyone must use the *same* token, or the machines cannot talk to each other.

---

## Step 2 — Run the join script

In Git-Bash, go to your GRIP folder (usually `~/.grip`) and run one of these.

**Easiest — it will prompt you to paste the token (hidden as you type):**

```bash
bash scripts/grip-windows-mesh.sh
```

**Or paste the token up front as an environment value:**

```bash
GRIP_MESH_TOKEN='paste-the-token-here' bash scripts/grip-windows-mesh.sh
```

The script figures out which of the three team machines is *yours* and wires you
to the **other two** automatically. If for any reason it cannot detect that, you
can name the two peers yourself:

- **If you are A>> (desktop-6kg0vq4):**

  ```bash
  bash scripts/grip-windows-mesh.sh --peer 100.64.0.10 --peer 100.64.0.10
  ```

- **If you are F>> (fabio-desktop):**

  ```bash
  bash scripts/grip-windows-mesh.sh --peer 100.64.0.10 --peer 100.64.0.10
  ```

(The three addresses are: V>> `100.64.0.10`, A>> `100.64.0.10`,
F>> `100.64.0.10`. You always connect to the two that are **not** you.)

### What you will see

The script narrates each step in plain English: it checks Node and Python,
makes sure the mesh software is present and built, writes the token, writes the
two peers, starts the Bus and Relay, and finally checks the Bus is healthy. When
it finishes it prints a short "Next step" note.

---

## Step 3 — Confirm you are connected

The last thing the script does is check the Bus. A healthy result looks like:

```
[win-mesh]     bus health: OK (http://127.0.0.1:9474/health)
```

Then tell V>> you are up. **V>> confirms from his side** — from his GRIP session
he can list the connected sessions or send a quick broadcast; if it comes back
having reached your machine, all three of us are on the mesh.

---

## Step 4 (optional but recommended) — Stay connected after a reboot

By default the Bus and Relay run until you shut down or log off. To have them
**start again automatically every time you log in**, run the script once more
with `--persist`:

```bash
bash scripts/grip-windows-mesh.sh --persist
```

This installs three Windows **Scheduled Tasks** (Bus, Relay, Client) that start
at logon and restart themselves if they ever stop — the Windows equivalent of
what V>>'s Mac does automatically. No administrator rights are needed; they run
as you.

To remove them later:

```bash
powershell -ExecutionPolicy Bypass -File deploy/windows/install-windows.ps1 -Uninstall
```

---

## For V>>: driving A>>'s machine over SSH

A>> has OpenSSH Server enabled, so V>> can run all of the above without A>> being
at the keyboard:

```bash
ssh "Andre Theart"@100.64.0.10
```

(The quotes matter — the username has a space.) Then run the Step 2 command in
that SSH session. Hand A>> the token the same way, or paste it inline with the
`GRIP_MESH_TOKEN='...'` form.

---

## If something does not work

- **"no token provided"** — you skipped the token. Re-run Step 2 and paste it
  when prompted, or use the `GRIP_MESH_TOKEN='...'` form.
- **"node not found" / "no python found"** — install the missing tool
  (Prerequisites above), then re-run. Re-running is always safe.
- **"websockets is missing"** — run the exact `pip install websockets` command
  the script prints, then re-run.
- **Bus not reachable on :9474** — look at the log the script mentions
  (`~/.grip-session-mesh/logs/bus.err.log`) or just re-run the script.
- **No GRIP warm-start banner when you open Claude Code** — your `~/.claude`
  is not pointing at `~/.grip`. Run `bash ~/.grip/bin/grip-harness-link`
  (Step 0) and follow its message, then re-open Claude Code.
- **Still stuck?** Send V>> the last ~20 lines the script printed. Nothing here
  is destructive, so it is always safe to try again.

---

## What the script never does (safety)

- It never puts the token into GRIP's code or any GitHub repo.
- It never makes up a new token (that would split us into two meshes that cannot
  talk).
- Re-running it does not duplicate anything — already-done steps are skipped.
