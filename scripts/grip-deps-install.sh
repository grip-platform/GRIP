#!/usr/bin/env bash
# GRIP Dependency Auto-Installer
#
# Installs missing Python packages (audit-driven + every requirements.txt),
# MCP-server deps (node `npm ci` + the `mcp` pip pkg for FastMCP servers),
# optional HAL python deps, and checks system CLI tools.
# Idempotent — safe to run multiple times. Drives pip via `python -m pip`
# so it works on both stdlib venvs and uv-created venvs (no `bin/pip` needed).
#
# Usage:
#   bash scripts/grip-deps-install.sh           # install all missing
#   bash scripts/grip-deps-install.sh --check   # dry-run, report only
#   bash scripts/grip-deps-install.sh --deps    # install + generate requirements.txt

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
VENV_DIR="$CLAUDE_DIR/venv"
# Resolve the venv interpreter robustly: uv-created venvs symlink python3->python
# and OMIT the `bin/pip` console script, so we always drive pip via `python -m pip`
# rather than a `bin/pip` FILE (the uv-venv breakage this installer must survive).
VENV_PYTHON="$VENV_DIR/bin/python3"
[ -x "$VENV_PYTHON" ] || VENV_PYTHON="$VENV_DIR/bin/python"
AUDIT_SCRIPT="$CLAUDE_DIR/scripts/grip-deps-audit.py"

# Venv-agnostic pip runner (works on stdlib venvs AND uv venvs).
vpip() { "$VENV_PYTHON" -m pip "$@"; }

# Colours (disabled if not a terminal)
if [ -t 1 ]; then
    GREEN='\033[0;32m'
    RED='\033[0;31m'
    YELLOW='\033[1;33m'
    NC='\033[0m'
else
    GREEN='' RED='' YELLOW='' NC=''
fi

info()  { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; }

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

preflight() {
    if [ ! -d "$VENV_DIR" ]; then
        fail "venv not found at $VENV_DIR"
        echo "  Run: python3 -m venv $VENV_DIR"
        exit 1
    fi

    if ! "$VENV_PYTHON" -m pip --version &>/dev/null; then
        fail "pip not available via '$VENV_PYTHON -m pip'"
        echo "  Bootstrap it: $VENV_PYTHON -m ensurepip --upgrade"
        exit 1
    fi

    if [ ! -f "$AUDIT_SCRIPT" ]; then
        fail "Audit script not found at $AUDIT_SCRIPT"
        exit 1
    fi

    info "venv found at $VENV_DIR"
}

# ---------------------------------------------------------------------------
# Python dependency installation
# ---------------------------------------------------------------------------

install_python_deps() {
    local audit_json
    # `grip-deps-audit.py` exits non-zero when deps are missing but still emits
    # valid JSON on stdout — tolerate the exit code (set -e would kill us here).
    audit_json=$("$VENV_PYTHON" "$AUDIT_SCRIPT" --json 2>/dev/null) || true

    local missing_count
    missing_count=$(echo "$audit_json" | jq '.summary.python_missing')

    if [ "$missing_count" = "0" ]; then
        info "All Python dependencies installed"
        return 0
    fi

    warn "$missing_count missing Python packages detected"

    local pip_names
    pip_names=$(echo "$audit_json" | jq -r '.python.missing | to_entries[] | .value.pip_name' | sort -u)

    if [ "$CHECK_ONLY" = "true" ]; then
        echo "  Would install:"
        echo "$pip_names" | while read -r pkg; do
            echo "    - $pkg"
        done
        return 0
    fi

    echo "  Installing: $(echo "$pip_names" | tr '\n' ' ')"
    # shellcheck disable=SC2086
    vpip install $pip_names 2>&1 | tail -3
    info "Python packages installed"
}

# ---------------------------------------------------------------------------
# requirements.txt manifests across the repo
# ---------------------------------------------------------------------------

# List tracked requirements*.txt relative to the repo root in $1. Prefers
# `git ls-files` (dependency dirs are gitignored, so never listed). Falls
# back to find(1) ONLY for non-git checkouts (starter kit, tarball) or when
# git itself is absent — the #288 DEFER: a missing .git must not silently
# skip dependency install. Inside a git repo an empty ls-files legitimately
# means "no manifests" and find would resurrect gitignored junk, so the
# fallback never fires there.
list_requirements_manifests() {
    local root="$1" out
    out=$(git -C "$root" ls-files 2>/dev/null \
        | grep -E '(^|/)requirements.*\.txt$' \
        | grep -vE 'node_modules|/venv/|site-packages' || true)
    # Fallback iff root is NOT a git checkout of any shape: rev-parse succeeds
    # for .git directories AND .git files (worktrees, submodules) and fails
    # when the git binary is absent. Inside a real checkout an empty ls-files
    # means "no manifests" — find would resurrect ignored junk.
    if [ -z "$out" ] && ! git -C "$root" rev-parse --git-dir >/dev/null 2>&1; then
        out=$(find "$root" -name 'requirements*.txt' \
            -not -path '*/node_modules/*' -not -path '*/venv/*' \
            -not -path '*/site-packages/*' -not -path '*/.git/*' \
            -not -path '*/__pycache__/*' -not -path '*/.tox/*' \
            -not -path '*/.direnv/*' -not -path '*/envs/*' \
            -not -path '*/.cache/*' 2>/dev/null \
            | sed "s|^$root/||" | sort || true)
    fi
    # Line-oriented output: trailing newline when non-empty (command
    # substitution at the callers strips it, so they are unaffected).
    if [ -n "$out" ]; then
        printf '%s\n' "$out"
    fi
}

# Discover every tracked requirements*.txt via git (dependency dirs are
# gitignored, so never listed) and `pip install -r` each. pyproject packages
# (oss/, packages/) are installed on demand, NOT here — the root pyproject has
# no [build-system] and would try to package the whole tree.
install_requirements_files() {
    echo ""
    echo "Requirements manifests (pip install -r):"
    local reqs
    reqs=$(list_requirements_manifests "$CLAUDE_DIR")
    if [ -z "$reqs" ]; then
        warn "no requirements.txt manifests found"
        return 0
    fi
    while IFS= read -r req; do
        [ -z "$req" ] && continue
        local path="$CLAUDE_DIR/$req"
        [ -f "$path" ] || continue
        if [ "$CHECK_ONLY" = "true" ]; then
            echo "  Would install: -r $req"
        else
            echo "  -r $req"
            vpip install -q -r "$path" 2>&1 | tail -2 || warn "failed: $req"
        fi
    done <<< "$reqs"
}

# ---------------------------------------------------------------------------
# MCP server dependencies (node + python stdio servers)
# ---------------------------------------------------------------------------

# The stdio MCP servers carry deps outside the audited GRIP import graph: node
# servers need `npm ci`; the FastMCP python servers (grip-hear, code-index) need
# the `mcp` pip package, which is NOT declared in any requirements.txt. (grip-hear
# also *optionally* uses librosa for its tempo/key floor; it degrades gracefully
# without it, so librosa is intentionally NOT force-installed here.)
#
# A server that tracks a lockfile gets exactly the versions its tests ran
# against: pnpm-lock.yaml with `pnpm install --frozen-lockfile`, and
# package-lock.json with `npm ci`. Only a server with no lockfile resolves its
# version ranges afresh. With no pnpm on PATH, npx fetches the pnpm version CI
# uses (.github/workflows/grip-channel-tests.yml).
GRIP_PNPM_VERSION="${GRIP_PNPM_VERSION:-11.8.0}"
grip_node_install() {
    local dir="$1"
    if [ -f "$dir/pnpm-lock.yaml" ] && command -v pnpm &>/dev/null; then
        (cd "$dir" && pnpm install --frozen-lockfile)
    elif [ -f "$dir/pnpm-lock.yaml" ]; then
        (cd "$dir" && npx --yes "pnpm@$GRIP_PNPM_VERSION" install --frozen-lockfile)
    elif [ -f "$dir/package-lock.json" ]; then
        (cd "$dir" && npm ci)
    else
        (cd "$dir" && npm install)
    fi
}

install_mcp_server_deps() {
    echo ""
    echo "MCP server dependencies:"
    local d name
    for d in "$CLAUDE_DIR"/mcp-servers/*/; do
        [ -f "${d}package.json" ] || continue
        name=$(basename "$d")
        if [ -d "${d}node_modules" ]; then
            info "$name: node_modules present"
        elif [ "$CHECK_ONLY" = "true" ]; then
            echo "  Would install from the tracked lockfile: $name"
        elif command -v npm &>/dev/null; then
            echo "  installing from the tracked lockfile: $name"
            grip_node_install "${d%/}" 2>&1 | tail -2 \
                || warn "node install failed: $name"
        else
            warn "npm not found — cannot install node deps for $name"
        fi
    done
    if "$VENV_PYTHON" -c "import mcp.server.fastmcp" &>/dev/null; then
        info "python 'mcp' present (grip-hear, code-index)"
    elif [ "$CHECK_ONLY" = "true" ]; then
        echo "  Would install python package: mcp"
    else
        # PIN <2 — this is load-bearing, not caution.
        #
        # mcp 2.0.0 REMOVED the mcp.server.fastmcp module. Every GRIP FastMCP
        # server imports `from mcp.server.fastmcp import FastMCP` (code-index,
        # grip-hear), so an unpinned install resolves to 2.x and those servers die
        # with "No Python with 'mcp' package found" — while pip reports mcp as
        # installed. Worse, the guard above tests exactly that import, so an
        # unpinned installer re-installs 2.x on every run and never converges:
        # the check stays red and the fix keeps reapplying the cause.
        echo "  pip install 'mcp<2'"
        vpip install -q "mcp<2" 2>&1 | tail -2 || warn "failed to install python 'mcp'"
    fi
}

# ---------------------------------------------------------------------------
# HAL Python dependencies (best-effort, non-fatal)
# ---------------------------------------------------------------------------

# HAL is mostly TypeScript; its only python manifest today is
# apps/copilot-api/requirements.txt. Installed into HAL's OWN venv to avoid
# polluting the GRIP venv; if HAL has no venv we report the create command
# rather than mixing HAL deps into GRIP.
install_hal_deps() {
    local HAL_DIR="${HAL_DIR:-$HOME/.hal}"
    [ -d "$HAL_DIR" ] || return 0
    echo ""
    echo "HAL Python dependencies ($HAL_DIR):"
    local hal_reqs
    hal_reqs=$(list_requirements_manifests "$HAL_DIR")
    if [ -z "$hal_reqs" ]; then
        info "no HAL python requirements"
        return 0
    fi
    local HAL_PY="$HAL_DIR/venv/bin/python3"
    [ -x "$HAL_PY" ] || HAL_PY="$HAL_DIR/venv/bin/python"
    if [ ! -x "$HAL_PY" ]; then
        warn "HAL venv absent — skipping HAL python deps (not mixing into GRIP venv)"
        echo "    Create: python3 -m venv $HAL_DIR/venv"
        return 0
    fi
    while IFS= read -r req; do
        [ -z "$req" ] && continue
        local path="$HAL_DIR/$req"
        [ -f "$path" ] || continue
        if [ "$CHECK_ONLY" = "true" ]; then
            echo "  Would install: -r $req (via HAL venv)"
        else
            echo "  -r $req"
            "$HAL_PY" -m pip install -q -r "$path" 2>&1 | tail -2 || warn "failed: $req"
        fi
    done <<< "$hal_reqs"
}

# ---------------------------------------------------------------------------
# System tool checks
# ---------------------------------------------------------------------------

check_system_tools() {
    echo ""
    echo "System CLI Tools:"

    local platform
    platform=$(uname -s)
    local pkg_cmd="brew install"
    if [ "$platform" = "Linux" ]; then
        pkg_cmd="apt install"
    fi

    local all_ok=true

    check_tool "rg"          "ripgrep"                               "$pkg_cmd" && true || all_ok=false
    check_tool "fd"          "fd"                                    "$pkg_cmd" && true || all_ok=false
    check_tool "jq"          "jq"                                    "$pkg_cmd" && true || all_ok=false
    check_tool "bat"         "bat"                                   "$pkg_cmd" && true || all_ok=false
    check_tool "git-crypt"   "git-crypt"                             "$pkg_cmd" && true || all_ok=false
    check_tool "gh"          "gh"                                    "$pkg_cmd" && true || all_ok=false
    check_tool "cloudflared" "cloudflare/cloudflare/cloudflared"     "$pkg_cmd" && true || all_ok=false

    # macOS built-ins (no install needed)
    if command -v caffeinate &>/dev/null; then
        info "caffeinate (macOS built-in)"
    elif [ "$platform" = "Darwin" ]; then
        warn "caffeinate not found (should be built-in on macOS)"
    fi

    if [ "$all_ok" = "true" ]; then
        info "All system tools available"
    fi
}

check_tool() {
    local cmd="$1" pkg="$2" installer="$3"
    if command -v "$cmd" &>/dev/null; then
        info "$cmd ($(command -v "$cmd"))"
        return 0
    else
        fail "$cmd not found"
        echo "    $installer $pkg"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Requirements.txt generation
# ---------------------------------------------------------------------------

generate_requirements() {
    if [ "$GEN_REQUIREMENTS" = "true" ]; then
        echo ""
        "$VENV_PYTHON" "$AUDIT_SCRIPT" --requirements
    fi
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print_summary() {
    local audit_json
    # `grip-deps-audit.py` exits non-zero when deps are missing but still emits
    # valid JSON on stdout — tolerate the exit code (set -e would kill us here).
    audit_json=$("$VENV_PYTHON" "$AUDIT_SCRIPT" --json 2>/dev/null) || true

    echo ""
    echo "============================================================"
    echo "GRIP Dependency Summary"
    echo "============================================================"
    echo "  Python installed: $(echo "$audit_json" | jq '.summary.python_installed')"
    echo "  Python missing:   $(echo "$audit_json" | jq '.summary.python_missing')"
    echo "  System tools OK:  $(echo "$audit_json" | jq '.summary.system_tools_found')"
    echo "  System missing:   $(echo "$audit_json" | jq '.summary.system_tools_missing')"
    echo "============================================================"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

CHECK_ONLY=false
GEN_REQUIREMENTS=false

for arg in "$@"; do
    case "$arg" in
        --check) CHECK_ONLY=true ;;
        --deps)  GEN_REQUIREMENTS=true ;;
        --help|-h)
            echo "Usage: $0 [--check] [--deps]"
            echo "  --check   Dry-run, report only (no installs)"
            echo "  --deps    Also generate requirements.txt"
            echo ""
            echo "Installs: audited pip pkgs, every requirements.txt (GRIP + HAL),"
            echo "          MCP-server deps (npm ci + python 'mcp'). Idempotent."
            exit 0
            ;;
    esac
done

preflight
install_python_deps
install_requirements_files
install_mcp_server_deps
install_hal_deps
check_system_tools
generate_requirements
print_summary
