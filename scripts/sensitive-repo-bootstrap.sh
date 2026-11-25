#!/usr/bin/env bash
# scripts/sensitive-repo-bootstrap.sh
# Create a symmetric git-crypt private repo for sensitive financial / client data.
# Proven recipe (manually executed 2026-05-19 for CodeTonight-SA/exampleorg-financials).
# Usage: sensitive-repo-bootstrap.sh --dir <path> --repo <name> [--org CodeTonight-SA] [--desc "..."]
# See rules/no-sensitive-info-in-grip.md

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
ORG="CodeTonight-SA"
DIR=""
REPO=""
DESC=""

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir)  DIR="$2";  shift 2 ;;
        --repo) REPO="$2"; shift 2 ;;
        --org)  ORG="$2";  shift 2 ;;
        --desc) DESC="$2"; shift 2 ;;
        *) echo "ERROR: unknown argument: $1" >&2; exit 1 ;;
    esac
done

# ── Validation ─────────────────────────────────────────────────────────────────
if [[ -z "$DIR" || -z "$REPO" ]]; then
    echo "ERROR: --dir and --repo are required" >&2
    echo "Usage: $0 --dir <path> --repo <name> [--org <org>] [--desc \"description\"]" >&2
    exit 1
fi

KEY_PATH="$HOME/CodeTonight/.${REPO}-git-crypt.key"

echo "[sensitive-repo-bootstrap] Creating ${ORG}/${REPO} at ${DIR}"

# ── Step 1: directory ──────────────────────────────────────────────────────────
mkdir -p "$DIR"
cd "$DIR"

# ── Step 2: git init ───────────────────────────────────────────────────────────
# The branch is named here (git 2.28 or later), because step 10 pushes `main` and
# a machine whose git still defaults to `master` failed that push (GRIP#9156).
git init -q -b main

# ── Step 3: git-crypt init (NO -q flag — -q is invalid and silently no-ops) ────
# This was the exact bug that shipped plaintext on the first manual attempt.
echo "[sensitive-repo-bootstrap] Initialising git-crypt (symmetric key mode)..."
if ! git-crypt init 2>&1; then
    echo "ERROR: git-crypt init failed — encryption not initialised. Aborting." >&2
    exit 1
fi

# ── Step 4: export key ────────────────────────────────────────────────────────
mkdir -p "$(dirname "$KEY_PATH")"
git-crypt export-key "$KEY_PATH"
chmod 600 "$KEY_PATH"

if [[ ! -s "$KEY_PATH" ]]; then
    echo "ERROR: key export produced an empty file at ${KEY_PATH}. Aborting." >&2
    exit 1
fi
echo "[sensitive-repo-bootstrap] Key exported to ${KEY_PATH}"

# ── Step 5: .gitattributes (all files encrypted except manifests) ─────────────
cat > .gitattributes <<'GITATTR'
* filter=git-crypt diff=git-crypt
.gitattributes !filter !diff
.gitignore !filter !diff
README.md !filter !diff
GITATTR

# ── Step 6: .gitignore ────────────────────────────────────────────────────────
cat > .gitignore <<'GITIGNORE'
*.key
.DS_Store
__pycache__/
*.pyc
GITIGNORE

# ── Step 7: require a plaintext README.md ────────────────────────────────────
if [[ ! -f README.md ]]; then
    cat > README.md <<README
# ${REPO}

Sensitive financial / client-commercial data for ${ORG}.

All files except this README, .gitattributes, and .gitignore are encrypted
with a symmetric git-crypt key stored at \`~/CodeTonight/.${REPO}-git-crypt.key\`.

**Key is NOT stored in this repo or on GitHub.**
See \`rules/no-sensitive-info-in-grip.md\` in grip-platform/GRIP for the mandate.
README
fi

# ── Step 8: initial commit ───────────────────────────────────────────────────
git add .
git -c user.name="V>>" -c user.email="licensing@codetonight.co.za" \
    commit -m "feat: initialise ${REPO} with symmetric git-crypt encryption

Primary Author: V>>"

# ── Step 9: create private GitHub repo (no --license, no --gitignore) ─────────
# Never pass --license or --gitignore: server-side init commit is author-attributed
# and would leak identity — see feedback_gh_repo_create_license_author_leak.md
echo "[sensitive-repo-bootstrap] Creating private GitHub repo ${ORG}/${REPO}..."
GH_DESC_ARG=""
if [[ -n "$DESC" ]]; then
    GH_DESC_ARG="--description"
fi

if [[ -n "$DESC" ]]; then
    gh repo create "${ORG}/${REPO}" --private --description "$DESC"
else
    gh repo create "${ORG}/${REPO}" --private
fi

# ── Step 10: add origin ONLY — never any client remote ───────────────────────
git remote add origin "https://github.com/${ORG}/${REPO}.git"
git push -u origin main

# ── Step 11: mandatory remote verification ────────────────────────────────────
echo "[sensitive-repo-bootstrap] Verifying encryption on remote..."

# Find the first encrypted file (not README/.gitattributes/.gitignore)
FIRST_ENCRYPTED=""
while IFS= read -r tracked_file; do
    case "$tracked_file" in
        README.md|.gitattributes|.gitignore) continue ;;
        *) FIRST_ENCRYPTED="$tracked_file"; break ;;
    esac
done < <(git ls-files)

if [[ -z "$FIRST_ENCRYPTED" ]]; then
    echo "[sensitive-repo-bootstrap] WARNING: no encrypted files found — repo contains only manifests."
    echo "[sensitive-repo-bootstrap] Add sensitive files and re-verify manually:"
    echo "  gh api repos/${ORG}/${REPO}/contents/<file> --jq .content | base64 -d | head -c 16 | xxd"
else
    MAGIC=$(gh api "repos/${ORG}/${REPO}/contents/${FIRST_ENCRYPTED}" --jq '.content' \
        | base64 -d 2>/dev/null | head -c 10 | xxd || true)
    if echo "$MAGIC" | grep -q "0047 4954 4352 5950 5400"; then
        echo "[sensitive-repo-bootstrap] VERIFIED: ${FIRST_ENCRYPTED} is \0GITCRYPT\0 ciphertext on remote."
    else
        echo "ERROR: remote verification FAILED — ${FIRST_ENCRYPTED} is NOT git-crypt ciphertext." >&2
        echo "Raw magic bytes: ${MAGIC}" >&2
        exit 1
    fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════"
echo " SENSITIVE REPO READY"
echo " Repo:    ${ORG}/${REPO}"
echo " Local:   ${DIR}"
echo " Key:     ${KEY_PATH}"
echo " Remote:  https://github.com/${ORG}/${REPO}"
echo " Remotes: origin ONLY (no client remote)"
echo "═══════════════════════════════════════════════════════"
