---
name: sensitive-repo-bootstrap
description: Bootstrap a private GitHub repo where every sensitive file is git-crypt encrypted at rest, so files appear as ciphertext on GitHub and the symmetric key never leaves the machine. Use for invoices, contracts, or client-commercial documents.
---

# /sensitive-repo-bootstrap — Encrypted Private GitHub Repo for Sensitive Documents

Bootstrap a private GitHub repository where every sensitive file is git-crypt encrypted
at rest on the remote. Files appear as unreadable ciphertext on GitHub; the symmetric key
never leaves the local machine.

**Use for:** NDAs, partnership roadmaps, legal correspondence, financials, confidential strategy
documents — anything that must be version-controlled but never readable by anyone without the key.

## Usage

```
/sensitive-repo-bootstrap
  directory: ~/path/to/local/dir   (must exist, can have files already)
  repo: my-repo-name
  org: CodeTonight-SA              (default)
  description: "Brief description for GitHub"
  encrypt: "*.md *.pdf *.docx *.xlsx *.doc"  (default — covers all common doc types)
```

## Execution Protocol

### Step 1: Pre-flight

```bash
# Confirm git-crypt is installed
which git-crypt && git-crypt --version

# Confirm gh auth and org access
gh auth status
gh api /orgs/<ORG> --jq '.login'

# List what's in the target directory (no dependency folder reads)
ls -la <DIRECTORY>

# Confirm not already a git repo
test -d <DIRECTORY>/.git && echo "ALREADY GIT REPO" || echo "CLEAN"
```

If already a git repo with a remote — HALT and ask whether to add git-crypt to an existing repo
(different flow, not covered by this skill).

### Step 2: Initialise git and git-crypt

```bash
cd <DIRECTORY>
git init
git-crypt init
```

### Step 3: Write .gitattributes (encrypt all sensitive types)

Create `.gitattributes` in the repo root:

```
*.md filter=git-crypt diff=git-crypt
*.pdf filter=git-crypt diff=git-crypt
*.docx filter=git-crypt diff=git-crypt
*.doc filter=git-crypt diff=git-crypt
*.xlsx filter=git-crypt diff=git-crypt
*.xls filter=git-crypt diff=git-crypt
*.png filter=git-crypt diff=git-crypt
*.jpg filter=git-crypt diff=git-crypt
*.jpeg filter=git-crypt diff=git-crypt
```

Add a `.gitignore` that excludes `*.key` and `.DS_Store`.

### Step 4: Export symmetric key to safe path

```bash
KEY_PATH=~/CodeTonight/.<REPO-NAME>-git-crypt.key
git-crypt export-key "$KEY_PATH"
echo "Key written to $KEY_PATH — back this up separately. It NEVER goes in the repo."
```

The key path must be:
- **Outside** the repo directory
- Named `.<repo-name>-git-crypt.key` (hidden file)
- Never added to git (`.gitignore` covers `*.key`)

### Step 5: Create private GitHub repo

```bash
gh repo create <ORG>/<REPO-NAME> \
  --private \
  --description "<DESCRIPTION>"
```

### Step 6: Initial commit

Stage all files (git-crypt will encrypt .md/.pdf etc. automatically at commit time):

```bash
git add .gitattributes .gitignore <all other files>
git status  # verify correct files staged
git commit -m "feat: initialise <repo-name> — <brief description> (git-crypt encrypted)"
```

### Step 7: Add remote and push

```bash
git remote add origin https://github.com/<ORG>/<REPO-NAME>.git
git push -u origin main
```

### Step 8: Verify encryption (MANDATORY)

This step confirms the files on GitHub are unreadable ciphertext, not plaintext:

```bash
gh api repos/<ORG>/<REPO-NAME>/contents/<FIRST-ENCRYPTED-FILE> \
  --jq '.content' | base64 -d | head -c 20 | xxd
```

**Expected output** — the `GITCRYPT` magic bytes must appear at offset 0:
```
00000000: 0047 4954 4352 5950 5400 ...   .GITCRYPT.......
```

If you see readable text instead — git-crypt is NOT encrypting. Stop and diagnose before
treating the repo as secure. Common cause: the file was committed before `.gitattributes`
was staged (git-crypt only encrypts on commit, not retroactively).

### Step 9: Report

Output a summary:

```
SENSITIVE REPO READY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Repo:          https://github.com/<ORG>/<REPO-NAME>
Visibility:    PRIVATE
Encrypted:     <list of file types>
Key location:  ~/CodeTonight/.<REPO-NAME>-git-crypt.key

GITCRYPT verification: PASSED (magic bytes confirmed at offset 0)

IMPORTANT: Back up the key file separately.
Without it, the encrypted files are permanently unrecoverable.
Suggested: Bitwarden secure note, encrypted USB, or safe cloud backup.

To unlock on another machine:
  git clone https://github.com/<ORG>/<REPO-NAME>.git
  cd <REPO-NAME>
  git-crypt unlock ~/path/to/<REPO-NAME>-git-crypt.key
```

## Adding Files Later

Any `.md`, `.pdf`, `.docx` etc. added to the repo after bootstrap are automatically
encrypted at commit time — no extra steps needed. The `.gitattributes` rules apply
to all future commits.

```bash
cd ~/path/to/repo
cp /path/to/new-document.md .
git add new-document.md
git commit -m "docs: add <document name>"
git push
```

## Recovery / Unlock

If the local repo is locked (e.g. after a fresh clone on another machine):

```bash
git-crypt unlock ~/CodeTonight/.<REPO-NAME>-git-crypt.key
```

Files will be decrypted transparently in the working tree.

## Security Properties

| Property | Value |
|----------|-------|
| Algorithm | AES-256-GCM (git-crypt default) |
| Key type | Symmetric (32 bytes) |
| What GitHub sees | Encrypted blobs with GITCRYPT header |
| What collaborators without key see | Encrypted blobs — permanently unreadable |
| What git history reveals | Commit messages and file names (not content) |
| Key rotation | Not supported by git-crypt — use a fresh repo if key is compromised |

**Note on file names:** git-crypt encrypts file **content**, not file **names**. If the
filename itself is sensitive (e.g. `NDA-CompanyName.md`), consider using a neutral name
or a separate manifest file.

## Known Repos (CodeTonight)

| Repo | Purpose | Key |
|------|---------|-----|
| `CodeTonight-SA/5E-partnership` | 5ESol ISO 27001 CB roadmap + NDA | `~/CodeTonight/.5E-partnership-git-crypt.key` |
