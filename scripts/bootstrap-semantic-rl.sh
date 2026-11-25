#!/usr/bin/env bash
#
# Semantic RL++ Bootstrap Script
# Complete one-time setup for the self-improving orchestration system
#
# USAGE:
#   ~/.claude/scripts/bootstrap-semantic-rl.sh
#   ~/.claude/scripts/bootstrap-semantic-rl.sh --verify-only
#
# REQUIREMENTS:
#   - Python 3.9+
#   - pip3
#
# VERSION: 2.0.0
# DATE: 2025-12-22
# GEN: 153 - Venv isolation for reliable embeddings
#

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
LIB_DIR="$CLAUDE_DIR/lib"
SCRIPTS_DIR="$CLAUDE_DIR/scripts"
CONFIG_DIR="$CLAUDE_DIR/config"
VENV_DIR="$CLAUDE_DIR/venv"  # Gen 153: Dedicated venv for reliability

VERIFY_ONLY="${1:-}"

# Cross-platform venv Python resolution (shared utility)
source "${SCRIPTS_DIR}/lib/venv-utils.sh"

log() {
    echo "[$(date +%H:%M:%S)] $*"
}

log_success() {
    echo "[$(date +%H:%M:%S)] [OK] $*"
}

log_error() {
    echo "[$(date +%H:%M:%S)] [ERROR] $*" >&2
}

log_warn() {
    echo "[$(date +%H:%M:%S)] [WARN] $*"
}

check_python() {
    log "Checking Python..."

    if ! command -v python3 &>/dev/null; then
        log_error "python3 not found. Install Python 3.9+"
        return 1
    fi

    local version
    version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")

    local major minor
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)

    if [[ $major -lt 3 ]] || [[ $major -eq 3 && $minor -lt 9 ]]; then
        log_error "Python $version found, but 3.9+ required"
        return 1
    fi

    log_success "Python $version"
    return 0
}

# The venv gets its own pip from ensurepip (ensure_venv_pip), so a pip3 on PATH
# is not needed. Stock Ubuntu has none until python3-pip is installed, and the
# old pip3 check stopped every Linux bootstrap there (W1 stranger rehearsal,
# 2026-11-24). What a venv needs is a Python whose ensurepip works; Debian and
# Ubuntu ship that in python3-venv.
check_pip() {
    log "Checking that Python can give a venv its own pip..."

    if ! python3 -c "import ensurepip" 2>/dev/null; then
        log_error "python3 has no ensurepip, so a new venv would have no pip"
        log "On Debian or Ubuntu: sudo apt install python3-venv"
        return 1
    fi

    log_success "ensurepip available"
    return 0
}

# Gen 153: Create dedicated venv for embedding system
# Solves: pyenv vs system Python mismatch causing silent embedding failures
create_venv() {
    log "Setting up dedicated Python venv..."

    # Find the best Python to use
    local python_path=""

    # Priority 1: pyenv Python 3.13+
    if command -v pyenv &>/dev/null; then
        local pyenv_python
        pyenv_python=$(pyenv which python3 2>/dev/null || true)
        if [[ -n "$pyenv_python" && -x "$pyenv_python" ]]; then
            local pyenv_version
            pyenv_version=$("$pyenv_python" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
            local major minor
            major=$(echo "$pyenv_version" | cut -d. -f1)
            minor=$(echo "$pyenv_version" | cut -d. -f2)
            if [[ $major -ge 3 && $minor -ge 9 ]]; then
                python_path="$pyenv_python"
                log "  Using pyenv Python: $pyenv_version"
            fi
        fi
    fi

    # Priority 2: System Python (python3 preferred, python fallback for Windows)
    if [[ -z "$python_path" ]]; then
        if command -v python3 &>/dev/null; then
            python_path=$(command -v python3)
            log "  Using system Python: python3"
        elif command -v python &>/dev/null; then
            python_path=$(command -v python)
            log "  Using system Python: python"
        else
            log_error "No suitable Python found"
            return 1
        fi
    fi

    # Create venv if not exists
    if [[ -d "$VENV_DIR" ]] && resolve_venv_python "$VENV_DIR" >/dev/null 2>&1; then
        log_success "Venv already exists at $VENV_DIR"
        return 0
    fi

    log "  Creating venv at $VENV_DIR..."
    "$python_path" -m venv "$VENV_DIR" || {
        log_error "Failed to create venv"
        return 1
    }

    log_success "Venv created"
    return 0
}

# Decision-record signing needs $GRIP_SIGNING_REQUIREMENT (scripts/lib/venv-utils.sh).
# It goes in on its own, before anything optional, for the reason ensure_grip_venv
# installs it apart from requirements.txt: a build with no requirements.txt (the
# public GRIP ships none, measured 2026-11-24, GRIP#9370) or one package that
# fails to build must not leave the venv unable to sign. Non-fatal: install.sh's
# verify_signing_capability reports a venv that still cannot sign.
install_signing_backend() {
    local venv_python
    venv_python="$(resolve_venv_python "$VENV_DIR" || true)"
    if [[ "$venv_python" != "$VENV_DIR/"* ]]; then
        log_error "Venv Python not found; the signing backend was not installed"
        return 0
    fi

    log "Installing the signing backend ($GRIP_SIGNING_REQUIREMENT)..."
    ensure_venv_pip "$venv_python" || {
        log_error "venv has no pip; the signing backend was not installed"
        return 0
    }
    # --prefer-binary: cryptography 50.0.2 has no Intel Mac wheel, so without it
    # pip on an Intel Mac tries a Rust build instead of taking 48.0.1's wheel.
    if "$venv_python" -m pip install --quiet --prefer-binary "$GRIP_SIGNING_REQUIREMENT"; then
        log_success "Signing backend installed"
    else
        log_error "Could not install $GRIP_SIGNING_REQUIREMENT; decision records cannot be signed until it is"
        log "Retry: $venv_python -m pip install --prefer-binary '$GRIP_SIGNING_REQUIREMENT'"
    fi
    return 0
}

# _repo_file <relative-path>: print where <relative-path> is, looking in
# CLAUDE_DIR first and then in the checkout this script came from; return 1
# when neither holds it.
_repo_file() {
    local found="${CLAUDE_DIR:-$HOME/.claude}/$1"
    [[ -f "$found" ]] || found="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/$1"
    [[ -f "$found" ]] || return 1
    echo "$found"
}

# Install requirements-core.txt, the packages a fresh install needs (GRIP#9399),
# and then the full requirements.txt when this checkout has one. The public build
# ships only the core list, so on a stranger's machine the second step finds
# nothing, by design.
#
# The core install runs every time rather than after an import probe: pip skips
# what is already satisfied without going online, and an earlier early-return
# here kept cryptography>=48 off existing venvs (GRIP#5646, 2026-10-10).
# --prefer-binary takes the newest release with a wheel for this machine rather
# than compiling a newer one; cryptography 50.0.2 has no Intel Mac wheel.
install_python_deps() {
    log "Installing Python dependencies into venv..."

    local venv_python core
    venv_python="$(resolve_venv_python "$VENV_DIR")"

    if [[ -z "$venv_python" ]] || [[ ! -x "$venv_python" ]]; then
        log_error "Venv Python not found - run create_venv first"
        return 1
    fi

    # 4, not 1: no later heal can install packages a checkout does not list,
    # so install.sh fails the install on this code alone (GRIP#9487).
    core="$(_repo_file requirements-core.txt)" || {
        log_error "requirements-core.txt not found; this GRIP checkout is incomplete"
        return 4
    }

    ensure_venv_pip "$venv_python" || {
        log_error "venv has no pip and ensurepip failed; cannot install dependencies"
        log "Try: $venv_python -m ensurepip --upgrade"
        return 1
    }

    "$venv_python" -m pip install --quiet --prefer-binary -r "$core" || {
        log_error "Failed to install the core packages listed in $core"
        log "Try: $venv_python -m pip install --prefer-binary -r $core"
        return 1
    }
    log_success "Core packages installed"

    install_declared_requirements "$venv_python"
    return 0
}

# Install the project's full DECLARED dependencies (requirements.txt), on top of
# the core list above.
#
# Origin: 2026-09-20 release-readiness audit. This function is named
# install_python_deps and installed apsw, sqlite-vec and sqlite-lembed and
# nothing else, so `cryptography>=48` never reached the venv on the git-clone
# install path. Without it lib/precog/idr.py downgrades every decision record
# from a dual Ed25519 + ML-DSA-65 signature to a shared-key HMAC that
# requirements.txt itself calls forgeable -- and install.sh then logged
# "Installation verified successfully". The one-liner payload (scripts/blackbook-xp)
# already did install requirements; that asymmetry is what showed this path had
# been left behind.
#
# Non-fatal by design. A machine that cannot build cryptography (no compiler, no
# wheel for its platform, offline) must still get a working GRIP -- it gets an
# honest degraded one, announced by verify_signing_capability in install.sh
# rather than concealed behind an unqualified success line.
install_declared_requirements() {
    local venv_python="$1" req

    req="$(_repo_file requirements.txt)" || {
        log "No requirements.txt found; the core list alone is installed"
        return 0
    }

    log "Installing declared dependencies from $(basename "$req")..."
    ensure_venv_pip "$venv_python" || {
        log_error "venv has no pip; declared dependencies not installed"
        return 0
    }

    if "$venv_python" -m pip install --quiet --prefer-binary -r "$req"; then
        log_success "Declared dependencies installed"
    else
        log_error "Some declared dependencies failed to install; a feature that needs one will say so"
        log "Retry: $venv_python -m pip install --prefer-binary -r $req"
    fi
    return 0
}

# Ensure the venv has pip, self-healing a pip-less venv (one created with
# --without-pip, or a Python build whose venv ships no pip — which makes the
# `pip install` above fail with "No module named pip"). ensurepip is
# idempotent: a no-op when pip already exists. Returns non-zero only if pip is
# absent AND ensurepip cannot install it. Origin: 2026-06-22 fresh-install
# bootstrap hit "No module named pip" on a pip-less venv.
ensure_venv_pip() {
    local venv_python="$1"
    if "$venv_python" -m pip --version >/dev/null 2>&1; then
        return 0
    fi
    log "venv has no pip; bootstrapping it via ensurepip..."
    "$venv_python" -m ensurepip --upgrade >/dev/null 2>&1
}

# Check that the venv's own Python imports every package in requirements-core.txt.
# lib/core_requirements.py derives the list from the manifest, so the check
# cannot drift from what install_python_deps installed.
verify_python_deps() {
    log "Verifying Python dependencies in venv..."

    local venv_python core checker
    venv_python="$(resolve_venv_python "$VENV_DIR")"
    core="$(_repo_file requirements-core.txt)" || {
        log_error "requirements-core.txt not found; cannot verify the venv"
        return 1
    }
    checker="$(_repo_file lib/core_requirements.py)" || {
        log_error "lib/core_requirements.py not found; cannot verify the venv"
        return 1
    }

    "$venv_python" "$checker" check-imports --requirements "$core" || {
        log_error "Python dependency verification failed"
        return 1
    }

    log_success "All dependencies verified in venv"
    return 0
}

init_database() {
    log "Initializing database schema..."

    local venv_python
    venv_python="$(resolve_venv_python "$VENV_DIR")"

    "$venv_python" << 'PYEOF'
import sys
sys.path.insert(0, f"{__import__('os').environ.get('CLAUDE_DIR', __import__('pathlib').Path.home() / '.claude')}/lib")

from embeddings import EmbeddingEngine

engine = EmbeddingEngine()
engine.init_schema()
print("  Base schema initialized")
engine.close()
PYEOF

    if [[ $? -ne 0 ]]; then
        log_error "Failed to initialize database schema"
        return 1
    fi

    log_success "Database schema ready"
    return 0
}

init_kono_schema() {
    log "Initializing KONO schema..."

    local venv_python
    venv_python="$(resolve_venv_python "$VENV_DIR")"

    # Skip if kono_schema.py doesn't exist
    if [[ ! -f "$LIB_DIR/kono_schema.py" ]]; then
        log_warn "kono_schema.py not found, skipping KONO init"
        return 0
    fi

    CLAUDE_DIR="$CLAUDE_DIR" "$venv_python" "$LIB_DIR/kono_schema.py" init 2>&1 | while IFS= read -r line; do
        echo "  $line"
    done

    if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
        log_warn "KONO schema init failed (non-fatal)"
        return 0
    fi

    log_success "KONO schema initialized"
    return 0
}

init_threshold_tables() {
    log "Initializing threshold configuration..."

    local venv_python
    venv_python="$(resolve_venv_python "$VENV_DIR")"

    "$venv_python" << 'PYEOF'
import sys
import os
claude_dir = os.environ.get('CLAUDE_DIR', str(__import__('pathlib').Path.home() / '.claude'))
sys.path.insert(0, f"{claude_dir}/lib")

from embeddings import EmbeddingEngine

engine = EmbeddingEngine()
conn = engine.connect()

conn.execute('''
    CREATE TABLE IF NOT EXISTS threshold_config (
        concept_type TEXT PRIMARY KEY,
        current_threshold REAL NOT NULL,
        min_threshold REAL DEFAULT 0.2,
        max_threshold REAL DEFAULT 0.9,
        total_matches INTEGER DEFAULT 0,
        successful_matches INTEGER DEFAULT 0,
        last_calibrated TEXT,
        auto_adjust INTEGER DEFAULT 1,
        adjustment_sensitivity REAL DEFAULT 0.02
    )
''')

conn.execute('''
    CREATE TABLE IF NOT EXISTS match_history (
        id INTEGER PRIMARY KEY,
        prompt_text TEXT,
        concept_type TEXT,
        matched_concept TEXT,
        similarity REAL,
        threshold_used REAL,
        user_feedback TEXT,
        feedback_at TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
''')

conn.execute('CREATE INDEX IF NOT EXISTS idx_match_history_feedback ON match_history(user_feedback)')
conn.execute('CREATE INDEX IF NOT EXISTS idx_match_history_concept ON match_history(concept_type)')
conn.execute('CREATE INDEX IF NOT EXISTS idx_match_history_created ON match_history(created_at)')

defaults = [
    ('concept_intent', 0.40),
    ('concept_feedback', 0.25),
    ('concept_agent', 0.45),
    ('concept_skill', 0.45),
    ('concept_command', 0.45),
    ('checkpoint', 0.55),
    ('concept_solution', 0.40),
    ('concept_workflow', 0.40),
]

for concept_type, threshold in defaults:
    try:
        conn.execute('''
            INSERT INTO threshold_config (concept_type, current_threshold)
            VALUES (?, ?)
        ''', (concept_type, threshold))
    except:
        pass

print("  Threshold tables created")
print("  Default thresholds configured")
engine.close()
PYEOF

    if [[ $? -ne 0 ]]; then
        log_error "Failed to initialize threshold tables"
        return 1
    fi

    log_success "Threshold configuration ready"
    return 0
}

embed_concepts() {
    log "Pre-embedding concept library..."

    local venv_python
    venv_python="$(resolve_venv_python "$VENV_DIR")"

    if [[ ! -f "$SCRIPTS_DIR/embed-concepts.py" ]]; then
        log_error "embed-concepts.py not found"
        return 1
    fi

    if [[ ! -f "$CONFIG_DIR/concept-library.json" ]]; then
        log_error "concept-library.json not found"
        return 1
    fi

    "$venv_python" "$SCRIPTS_DIR/embed-concepts.py" 2>&1 | while IFS= read -r line; do
        echo "  $line"
    done
    local rc=${PIPESTATUS[0]}

    # 3 = no embedding server answered (embed-concepts.py NO_SERVER_EXIT): semantic
    # memory is optional, so it is a skip the caller reports, not a failure.
    if [[ $rc -eq 3 ]]; then
        log_warn "Concept embeddings skipped: no embedding server answered"
        return 3
    fi
    if [[ $rc -ne 0 ]]; then
        log_error "Failed to embed concepts"
        return 1
    fi

    log_success "Concepts embedded"
    return 0
}

# Run the live check and stop the bootstrap unless it passed. Its 3, no embedding
# server, ends the bootstrap with 3, "semantic memory is off", which the installer
# reports as such, not as a failure and not as "all systems operational" (GRIP#9706).
live_check_or_exit() {
    local rc=0
    verify_installation || rc=$?
    [[ $rc -eq 3 ]] && exit 3
    [[ $rc -eq 0 ]] || exit 1
}

verify_installation() {
    log "Verifying installation..."

    local venv_python
    venv_python="$(resolve_venv_python "$VENV_DIR")"

    "$venv_python" << 'PYEOF'
import sys
import os
claude_dir = os.environ.get('CLAUDE_DIR', str(__import__('pathlib').Path.home() / '.claude'))
sys.path.insert(0, f"{claude_dir}/lib")

from embeddings import get_engine
from embeddings.ollama_backend import EmbeddingServerUnreachable

engine = get_engine()
stats = engine.get_stats()

print(f"  Total embeddings: {stats['total_embeddings']}")
print(f"  Concept types: {len(stats['by_type'])}")

try:
    # Not from the cache: a cached vector passed this test with no server (GRIP#9706).
    vec = engine.embed_text('search past conversations', use_cache=False)
except EmbeddingServerUnreachable as exc:
    # No embedding server: one plain line, not a traceback. Exit 3 means "semantic
    # memory is off", as embed_concepts already reports it (GRIP#9706).
    print(f"  Embedding test: skipped, because semantic memory is off: {exc}")
    engine.close()
    sys.exit(3)
if vec:
    print(f"  Embedding test: PASS ({len(vec) // 4} dims)")
else:
    print("  WARNING: Embedding generation failed")
    sys.exit(1)

engine.close()
print("\n  All systems operational")
print("  (full schema/table check: scripts/verify-kono-install.sh)")
PYEOF
    local rc=$?

    # 3: no embedding server answered the live check; the caller reports it.
    if [[ $rc -eq 3 ]]; then
        log_warn "Semantic memory is off: no embedding server answered the live check"
        return 3
    fi
    if [[ $rc -ne 0 ]]; then
        log_error "Verification failed"
        return 1
    fi

    log_success "Installation verified"
    return 0
}

show_next_steps() {
    echo ""
    echo "========================================"
    echo "  Semantic RL++ Bootstrap Complete"
    echo "========================================"
    echo ""
    echo "Next steps:"
    echo ""
    echo "  1. Start a new Claude Code session:"
    echo "     $ claude"
    echo ""
    echo "  2. The system will auto-initialize via hooks"
    echo ""
    echo "  3. Say 'RL++' to confirm all systems loaded"
    echo ""
    echo "  4. Optional: Set up weekly maintenance cron:"
    echo "     0 3 * * 0 ~/.claude/scripts/weekly-maintenance.sh"
    echo ""
    echo "Files created:"
    echo "  - ~/.claude/venv/          (isolated Python environment)"
    echo "  - ~/.claude/embeddings.db  (vector database)"
    echo ""
    echo "Gen 153: Venv isolation ensures embeddings work reliably"
    echo "        regardless of pyenv/system Python configuration."
    echo ""
    echo "Dynamic learning is now active:"
    echo "  - Coherence gate prevents gibberish from scoring as novel"
    echo "  - Thresholds adjust based on your feedback"
    echo "  - Patterns emerge from usage"
    echo "  - Cross-project insights accumulate"
    echo ""
}

main() {
    echo ""
    echo "========================================"
    echo "  Semantic RL++ Bootstrap v2.0.0"
    echo "  (Gen 153: Venv Isolation)"
    echo "========================================"
    echo ""

    export CLAUDE_DIR

    if [[ "$VERIFY_ONLY" == "--verify-only" ]]; then
        log "Running verification only..."
        verify_python_deps || exit 1
        live_check_or_exit
        echo ""
        log_success "All systems operational"
        exit 0
    fi

    check_python || exit 1
    check_pip || exit 1
    create_venv || exit 1       # Gen 153: Create venv before installing deps
    install_signing_backend     # GRIP#9370: before anything optional; never fatal
    install_python_deps || exit $?   # 4 = the checkout is incomplete (GRIP#9487)
    verify_python_deps || exit 1
    init_database || exit 1
    init_kono_schema || true    # Gen 315: KONO schema (non-fatal)
    init_threshold_tables || exit 1
    local embed_rc=0
    embed_concepts || embed_rc=$?
    # 3: no embedding server, already reported in one line. The live check below
    # needs a server, so stop here and let the installer say semantic memory is off.
    [[ $embed_rc -eq 3 ]] && exit 3
    [[ $embed_rc -eq 0 ]] || exit 1
    live_check_or_exit

    show_next_steps
}

main "$@"
