#!/usr/bin/env bash
#
# Embeddings set-up, kept under the name that messages and people still use.
#
# Usage: ./scripts/embeddings-init.sh [--verify-only]
#
# This script used to set up the sqlite-lembed engine by hand: check its package,
# download its GGUF model (about 140 MB), and build its schema. sqlite-lembed was
# retired in W6 (H218); KONO embeds through the Ollama backend, which never reads
# that model. The maintained set-up is scripts/bootstrap-semantic-rl.sh, which
# installs the core packages (requirements-core.txt), checks them with the venv's
# own Python, and prepares KONO. This script runs it, with any arguments given.
#
# Before GRIP#9399 this script stopped at its sqlite-lembed check, so it never
# downloaded anything. Once that check was gone it went on to fetch a model nothing
# reads, and Codex flagged it on GRIP#9435.

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"

echo "embeddings-init.sh runs the maintained set-up: $CLAUDE_DIR/scripts/bootstrap-semantic-rl.sh"
exec bash "$CLAUDE_DIR/scripts/bootstrap-semantic-rl.sh" "$@"
