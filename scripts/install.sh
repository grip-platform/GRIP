#!/usr/bin/env bash
#
# GRIP Installation Script
# Handles fresh install, merge, and upgrade scenarios.
#
# Usage: ./install.sh [--mode clone-home|symlink|copy] [--non-interactive]
#
# Scenarios:
#   A) Fresh Install - No existing ~/.claude
#   B) Merge Install - Existing ~/.claude with user config
#   C) Upgrade - Existing GRIP installation
#

set -euo pipefail

GRIP_VERSION="9.0.0"
# GRIP_REPO_URL installs from another copy of the repository, such as a local
# clone of a commit not yet published, so a release can rehearse exactly what it
# is about to push (GRIP#9330). Unset, it is the project's own address.
REPO_URL="${GRIP_REPO_URL:-https://github.com/grip-platform/GRIP.git}"
CLAUDE_DIR="$HOME/.claude"

INSTALL_ACTION=""  # "", "update", "verify"
NON_INTERACTIVE=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --update|--sync)
            INSTALL_ACTION="update"
            ;;
        --verify)
            INSTALL_ACTION="verify"
            ;;
        --launchd-agents)
            INSTALL_ACTION="launchd-agents"
            ;;
        --non-interactive)
            NON_INTERACTIVE=true
            ;;
        --mode)
            shift
            INSTALL_MODE_OVERRIDE="${1:-}"
            case "$INSTALL_MODE_OVERRIDE" in
                clone-home|symlink|copy) ;;
                *)
                    echo "[ERROR] --mode takes clone-home, symlink or copy" >&2
                    exit 1
                    ;;
            esac
            ;;
        --no-claude-install)
            SKIP_CLAUDE_INSTALL=true
            ;;
        -h|--help)
            echo "Usage: install.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --update, --sync       Update existing GRIP installation"
            echo "  --verify               Run verification checks only"
            echo "  --launchd-agents       Write the optional macOS background-agent plists (loads none)"
            echo "  --non-interactive      Skip interactive prompts"
            echo "  --no-claude-install    Skip Claude Code CLI installation"
            echo "  --mode MODE            Force installation mode (clone-home|symlink|copy)"
            echo "  -h, --help             Show this help"
            exit 0
            ;;
        *)
            echo "[ERROR] Unknown option: $1" >&2
            exit 1
            ;;
    esac
    shift
done

# ============================================================================
# CORE DIRECTORIES AND FILES
# ============================================================================

readonly CORE_DIRS=(
    "skills"
    "agents"
    "commands"
    "lib"
    "docs"
    "rules"
    "hooks"
    "config"
    "scripts"
    "bin"
    "output-styles"
)

# State directories (created during install, gitignored, hold runtime state)
readonly STATE_DIRS=(
    "state/approvals"
    "state/audit"
    "state/decisions"
    "state/features"
    "state/integrations"
    "state/memory"
    "state/rsi"
    "state/session"
)

readonly CORE_FILES=(
    "CLAUDE.md"
    "LICENSE"
    "README.md"
    "CODE_OF_CONDUCT.md"
    "CONTRIBUTING.md"
    "SECURITY.md"
    # The packages every install needs (GRIP#9399). The bootstrap and the
    # session-start heal read it from ~/.claude; without it here the symlink,
    # copy and merge installs got no core packages. requirements.txt stays out:
    # the public build does not ship it, and verification requires every entry.
    "requirements-core.txt"
)

readonly USER_FILES=(
    "facts/identity.md"
    "facts/team.md"
    "facts/people.md"
    ".env"
    ".onboarded"
    ".onboarded.json"
)

readonly RUNTIME_DIRS=(
    "projects"
    "plans"
    "todos"
    "file-history"
    "session-env"
    "cache"
    "contexts"
    "debug"
)

# ============================================================================
# COLOURS AND OUTPUT
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../lib/logging.sh"
# Refuses to destroy a populated directory for an empty one. One copy, so the
# ratio cannot drift between here and bin/grip -- which the inline comment this
# replaces warned about, while itself being one of the copies at risk.
source "${SCRIPT_DIR}/../lib/replace-guard.sh"

# ============================================================================
# DEPLOYMENT MODE DETECTION
# ============================================================================

detect_deployment_mode() {
    # Detects how GRIP is being deployed for security context
    if [[ -n "${GRIP_DEPLOYMENT_TOKEN:-}" ]]; then
        echo "managed"  # FDE-managed deployment
    elif [[ -f "/.dockerenv" ]] || grep -q docker /proc/1/cgroup 2>/dev/null; then
        echo "container"
    elif [[ -n "${SSH_CONNECTION:-}" ]]; then
        echo "vps"
    else
        echo "local"
    fi
}

# ============================================================================
# PLATFORM DETECTION
# ============================================================================

detect_platform() {
    case "$(uname -s)" in
        Darwin)  PLATFORM="macos" ;;
        Linux)   PLATFORM="linux" ;;
        MINGW*|MSYS*|CYGWIN*) PLATFORM="windows" ;;
        *)       PLATFORM="unknown" ;;
    esac

    # Windows symlink capability check
    if [[ "$PLATFORM" == "windows" ]]; then
        local test_dir="${TMPDIR:-/tmp}/grip_symlink_test_$$"
        mkdir -p "$test_dir"
        if ln -s "$test_dir" "${test_dir}_link" 2>/dev/null; then
            rm -rf "${test_dir}_link"
            SYMLINK_AVAILABLE=true
        else
            SYMLINK_AVAILABLE=false
        fi
        rm -rf "$test_dir"
    else
        SYMLINK_AVAILABLE=true
    fi

    export PLATFORM SYMLINK_AVAILABLE
}

# ============================================================================
# CROSS-PLATFORM PYTHON RESOLUTION (shared utility)
# ============================================================================

source "${SCRIPT_DIR}/lib/venv-utils.sh"

# ============================================================================
# SCENARIO DETECTION
# ============================================================================

detect_scenario() {
    if [[ ! -d "$CLAUDE_DIR" ]]; then
        echo "fresh"
        return
    fi

    # Check if it's already a GRIP installation
    if [[ -f "$CLAUDE_DIR/CLAUDE.md" ]] && grep -q "GRIP" "$CLAUDE_DIR/CLAUDE.md" 2>/dev/null; then
        # Check if it's a git repo (clone-as-home mode)
        if [[ -d "$CLAUDE_DIR/.git" ]]; then
            echo "upgrade-clone"
        # Check for symlink marker
        elif [[ -f "$CLAUDE_DIR/.grip-symlinked" ]]; then
            echo "upgrade-symlink"
        # Check for copy marker
        elif [[ -f "$CLAUDE_DIR/.grip-copy-source" ]]; then
            echo "upgrade-copy"
        else
            echo "upgrade-unknown"
        fi
    else
        # Existing ~/.claude but not GRIP
        echo "merge"
    fi
}

# ============================================================================
# USER PROMPTS
# ============================================================================

# _reply <prompt> <default>
#
# One answer to a prompt: read from the user, or under --non-interactive the
# default, without reading at all. Before GRIP#9330 the flag was set and never
# read, so the first menu still waited for input and an install went on only
# when input was closed. An empty answer, or closed input, also gives the
# default. Every prompt in this script goes through here.
_reply() {
    local prompt="$1" default="$2" answer=""
    if [[ "$NON_INTERACTIVE" == "true" ]]; then
        echo "${prompt}${default} (--non-interactive)" >&2
        echo "$default"
        return 0
    fi
    read -rp "$prompt" answer || true
    echo "${answer:-$default}"
}

# The mode to use for a chosen one: symlink falls back to copy where this system
# cannot make symlinks. The note goes to stderr because callers read stdout, and
# log_warn writes to stdout (lib/logging.sh).
_effective_mode() {
    if [[ "$1" == "symlink" && "$SYMLINK_AVAILABLE" != "true" ]]; then
        log_warn "Symlinks not available on this system, falling back to copy mode" >&2
        echo "copy"
    else
        echo "$1"
    fi
}

ask_installation_mode() {
    # --mode answers this menu (GRIP#9330: it was parsed and never read).
    if [[ -n "${INSTALL_MODE_OVERRIDE:-}" ]]; then
        _effective_mode "$INSTALL_MODE_OVERRIDE"
        return 0
    fi

    # Menu MUST go to stderr (not stdout) — callers capture this
    # function's stdout via $(ask_installation_mode) to read the chosen
    # mode. Echoing the menu to stdout swallows it silently and leaves
    # the operator staring at a bare "Choice [1/2/3]:" with no options.
    # Fatal at install time. Same anti-pattern applies to
    # ask_conflict_resolution and ask_merge_strategy below.
    echo "" >&2
    echo "GRIP Installation Mode" >&2
    echo "======================" >&2
    echo "" >&2
    echo "How would you like to install GRIP?" >&2
    echo "" >&2
    echo "1) Clone to ~/.claude (Recommended)" >&2
    echo "   - Repository becomes your config directory" >&2
    echo "   - Easy updates with git pull" >&2
    echo "   - Best for solo developers" >&2
    echo "" >&2
    echo "2) Symlink Overlay" >&2
    echo "   - Clone elsewhere, symlink core files" >&2
    echo "   - Keep repo separate from runtime" >&2
    echo "   - Best for contributors/developers" >&2
    echo "" >&2

    if [[ "$SYMLINK_AVAILABLE" == "false" ]]; then
        echo "3) Copy Mode (Windows Default)" >&2
        echo "   - Copy files instead of symlinks" >&2
        echo "   - Manual sync required for updates" >&2
        echo "" >&2
    fi

    choice="$(_reply "Choice [1/2/3]: " 1)"

    case "$choice" in
        1) echo "clone-home" ;;
        2) _effective_mode symlink ;;
        3) echo "copy" ;;
        *) echo "clone-home" ;;  # Default
    esac
}

ask_conflict_resolution() {
    local file="$1"
    local local_path="$2"
    local repo_path="$3"

    # Menu + diff to stderr; only the chosen verdict reaches stdout
    # for the $(ask_conflict_resolution ...) caller to read.
    # See note in ask_installation_mode for the stdout-capture rationale.
    echo "" >&2
    echo "Conflict Detected: $file" >&2
    echo "==================${file//?/=}" >&2
    echo "" >&2
    echo "Both local and repository versions exist." >&2
    echo "" >&2
    echo "1) Keep local version" >&2
    echo "2) Use repository version" >&2
    echo "3) View diff first" >&2
    echo "4) Merge manually (opens editor)" >&2
    echo "" >&2
    choice="$(_reply "Choice [1/2/3/4]: " 1)"

    case "$choice" in
        1) echo "keep-local" ;;
        2) echo "use-repo" ;;
        3)
            echo "" >&2
            echo "=== Diff: $file ===" >&2
            diff -u "$local_path" "$repo_path" 2>/dev/null | head -50 >&2 || true
            echo "" >&2
            echo "(Showing first 50 lines of diff)" >&2
            echo "" >&2
            ask_conflict_resolution "$file" "$local_path" "$repo_path"
            ;;
        4) echo "merge-manual" ;;
        *) echo "keep-local" ;;  # Safe default
    esac
}

ask_merge_strategy() {
    # Menu to stderr (caller captures stdout via $(ask_merge_strategy)).
    # See ask_installation_mode for the rationale.
    echo "" >&2
    echo "Existing ~/.claude Detected" >&2
    echo "============================" >&2
    echo "" >&2
    echo "I found an existing ~/.claude directory with your configuration." >&2
    echo "GRIP can merge its infrastructure without overwriting your files." >&2
    echo "" >&2
    echo "1) Backup and merge (Recommended)" >&2
    echo "   - Backup your config first" >&2
    echo "   - Install GRIP infrastructure" >&2
    echo "   - Preserve your customisations" >&2
    echo "" >&2
    echo "2) Replace entirely" >&2
    echo "   - Remove existing ~/.claude" >&2
    echo "   - Fresh GRIP installation" >&2
    echo "   - YOU WILL LOSE existing files" >&2
    echo "" >&2
    echo "3) Abort" >&2
    echo "   - Cancel installation" >&2
    echo "   - Keep current setup" >&2
    echo "" >&2
    choice="$(_reply "Choice [1/2/3]: " 1)"

    case "$choice" in
        1) echo "merge" ;;
        2) echo "replace" ;;
        3) echo "abort" ;;
        *) echo "merge" ;;  # Safe default
    esac
}

# ============================================================================
# BACKUP FUNCTIONS
# ============================================================================

backup_user_config() {
    local backup_dir="$HOME/.claude-backup-$(date +%Y%m%d-%H%M%S)"

    log_info "Backing up existing ~/.claude to $backup_dir"

    mkdir -p "$backup_dir"

    # Backup user files only
    for file in "${USER_FILES[@]}"; do
        if [[ -f "$CLAUDE_DIR/$file" ]]; then
            local dir
            dir=$(dirname "$backup_dir/$file")
            mkdir -p "$dir"
            cp "$CLAUDE_DIR/$file" "$backup_dir/$file"
            log_success "Backed up: $file"
        fi
    done

    # Backup any custom CLAUDE.md
    if [[ -f "$CLAUDE_DIR/CLAUDE.md" ]]; then
        cp "$CLAUDE_DIR/CLAUDE.md" "$backup_dir/CLAUDE.md.user"
        log_success "Backed up: CLAUDE.md (as CLAUDE.md.user)"
    fi

    echo "$backup_dir"
}

restore_user_config() {
    local backup_dir="$1"

    if [[ ! -d "$backup_dir" ]]; then
        log_warn "Backup directory not found: $backup_dir"
        return
    fi

    log_info "Restoring user configuration from backup"

    for file in "${USER_FILES[@]}"; do
        if [[ -f "$backup_dir/$file" ]]; then
            local dir
            dir=$(dirname "$CLAUDE_DIR/$file")
            mkdir -p "$dir"
            cp "$backup_dir/$file" "$CLAUDE_DIR/$file"
            log_success "Restored: $file"
        fi
    done
}

# ============================================================================
# INSTALLATION FUNCTIONS
# ============================================================================

install_fresh_clone() {
    log_info "Installing GRIP to ~/.claude (clone mode)..."

    if [[ -d "$CLAUDE_DIR" ]]; then
        log_error "~/.claude already exists. Cannot clone."
        exit 1
    fi

    git clone "$REPO_URL" "$CLAUDE_DIR"
    register_merge_drivers "$CLAUDE_DIR"

    # Create user files from templates
    create_user_files

    # Configure settings.json with absolute paths
    configure_settings_json
    install_git_hooks

    log_success "Fresh clone installation complete."
}

install_fresh_symlink() {
    local clone_dir="${1:-$HOME/Projects/grip}"

    log_info "Installing GRIP with symlinks..."
    log_info "Repository: $clone_dir"
    log_info "Runtime: $CLAUDE_DIR"

    # Clone if needed
    if [[ ! -d "$clone_dir" ]]; then
        log_info "Cloning GRIP repository..."
        mkdir -p "$(dirname "$clone_dir")"
        git clone "$REPO_URL" "$clone_dir"
    fi
    register_merge_drivers "$clone_dir"

    # Create runtime directory
    mkdir -p "$CLAUDE_DIR"

    # Create symlink marker
    echo "$clone_dir" > "$CLAUDE_DIR/.grip-symlinked"

    # Create symlinks
    create_symlinks "$clone_dir" "$CLAUDE_DIR"

    # Create user files from templates
    create_user_files

    # Configure settings.json with absolute paths
    configure_settings_json
    install_git_hooks

    log_success "Symlink installation complete."
}

install_fresh_copy() {
    local clone_dir="${1:-$HOME/Projects/grip}"

    log_info "Installing GRIP with copy mode (Windows compatible)..."
    log_info "Repository: $clone_dir"
    log_info "Runtime: $CLAUDE_DIR"

    # Clone if needed
    if [[ ! -d "$clone_dir" ]]; then
        log_info "Cloning GRIP repository..."
        mkdir -p "$(dirname "$clone_dir")"
        git clone "$REPO_URL" "$clone_dir"
    fi
    register_merge_drivers "$clone_dir"

    # Create runtime directory
    mkdir -p "$CLAUDE_DIR"

    # Copy core files
    copy_core_files "$clone_dir" "$CLAUDE_DIR"

    # Create copy marker with source location
    echo "$clone_dir" > "$CLAUDE_DIR/.grip-copy-source"

    # Create user files from templates
    create_user_files

    # Configure settings.json with absolute paths
    configure_settings_json
    install_git_hooks

    log_success "Copy installation complete."
}

# ============================================================================
# CORE FILE OPERATIONS
# ============================================================================

create_symlinks() {
    local source_dir="$1"
    local target_dir="$2"

    # Empty or "/" roots turn "$root/$dir" into a system path: called with no
    # arguments this loop once ran `ln -sfn /bin /bin` and broke the box.
    if ! grip_link_roots_are_sane "$source_dir" "$target_dir"; then
        log_error "Refusing to link: unsafe source ($source_dir) or target ($target_dir) root"
        return 1
    fi

    log_info "Creating symlinks..."

    # Symlink directories
    for dir in "${CORE_DIRS[@]}"; do
        if [[ -d "$source_dir/$dir" ]]; then
            # Remove existing if it's not already a symlink to the right place
            if [[ -e "$target_dir/$dir" ]] && [[ ! -L "$target_dir/$dir" ]]; then
                if ! grip_source_outweighs_target "$source_dir/$dir" "$target_dir/$dir"; then
                    log_warn "Skipped: $dir/ (source has ${GRIP_REPLACE_SRC_N} file(s) vs ${GRIP_REPLACE_TGT_N} installed - refusing to rm -rf)"
                    continue
                fi
                log_warn "Removing existing $dir (not a symlink)"
                rm -rf "$target_dir/$dir"
            fi
            ln -sfn "$source_dir/$dir" "$target_dir/$dir"
            log_success "Linked: $dir/"
        fi
    done

    # Symlink files
    for file in "${CORE_FILES[@]}"; do
        if [[ -f "$source_dir/$file" ]]; then
            ln -sf "$source_dir/$file" "$target_dir/$file"
            log_success "Linked: $file"
        fi
    done
}

copy_core_files() {
    local source_dir="$1"
    local target_dir="$2"

    # Empty or "/" roots turn "$root/$dir" into a system path: called with no
    # arguments this loop once ran `ln -sfn /bin /bin` and broke the box.
    if ! grip_link_roots_are_sane "$source_dir" "$target_dir"; then
        log_error "Refusing to copy: unsafe source ($source_dir) or target ($target_dir) root"
        return 1
    fi

    log_info "Copying core files..."

    # Use rsync if available (better for updates), else cp
    if command -v rsync &>/dev/null; then
        for dir in "${CORE_DIRS[@]}"; do
            if [[ -d "$source_dir/$dir" ]]; then
                if ! grip_source_outweighs_target "$source_dir/$dir" "$target_dir/$dir"; then
                    log_warn "Skipped: $dir/ (source has ${GRIP_REPLACE_SRC_N} file(s) vs ${GRIP_REPLACE_TGT_N} installed - refusing to --delete)"
                    continue
                fi
                rsync -av --delete "$source_dir/$dir/" "$target_dir/$dir/" >/dev/null
                log_success "Copied: $dir/"
            fi
        done

        for file in "${CORE_FILES[@]}"; do
            if [[ -f "$source_dir/$file" ]]; then
                rsync -av "$source_dir/$file" "$target_dir/$file" >/dev/null
                log_success "Copied: $file"
            fi
        done
    else
        # Fallback to cp
        for dir in "${CORE_DIRS[@]}"; do
            if [[ -d "$source_dir/$dir" ]]; then
                if ! grip_source_outweighs_target "$source_dir/$dir" "$target_dir/$dir"; then
                    log_warn "Skipped: $dir/ (source has ${GRIP_REPLACE_SRC_N} file(s) vs ${GRIP_REPLACE_TGT_N} installed - refusing to rm -rf)"
                    continue
                fi
                rm -rf "$target_dir/$dir"
                cp -r "$source_dir/$dir" "$target_dir/$dir"
                log_success "Copied: $dir/"
            fi
        done

        for file in "${CORE_FILES[@]}"; do
            if [[ -f "$source_dir/$file" ]]; then
                cp "$source_dir/$file" "$target_dir/$file"
                log_success "Copied: $file"
            fi
        done
    fi
}

create_user_files() {
    log_info "Setting up user configuration..."

    # Ensure facts directory exists
    mkdir -p "$CLAUDE_DIR/facts"

    # Create identity.md from template if it doesn't exist
    if [[ ! -f "$CLAUDE_DIR/facts/identity.md" ]]; then
        if [[ -f "$CLAUDE_DIR/facts/identity.md.template" ]]; then
            cp "$CLAUDE_DIR/facts/identity.md.template" "$CLAUDE_DIR/facts/identity.md"
            log_success "Created: facts/identity.md"
        fi
    else
        log_info "Preserved existing: facts/identity.md"
    fi

    # Create team.md from template if it doesn't exist
    if [[ ! -f "$CLAUDE_DIR/facts/team.md" ]]; then
        if [[ -f "$CLAUDE_DIR/facts/team.md.template" ]]; then
            cp "$CLAUDE_DIR/facts/team.md.template" "$CLAUDE_DIR/facts/team.md"
            log_success "Created: facts/team.md"
        fi
    else
        log_info "Preserved existing: facts/team.md"
    fi

    # Create runtime directories
    for dir in "${RUNTIME_DIRS[@]}"; do
        mkdir -p "$CLAUDE_DIR/$dir"
    done
    log_success "Created runtime directories"
}

configure_settings_json() {
    # $1 ("true"/"false", default "false"): skip the reference merge below
    # when the caller already composed settings.json against HEAD under the
    # plan-v4 ownership policy (run_update's clone-mode step). Every other
    # call site passes nothing, so a fresh install is unaffected.
    local already_composed="${1:-false}"
    if [[ "$already_composed" != "true" ]]; then
        log_info "Configuring settings.json..."
        source "$CLAUDE_DIR/lib/settings-merge.sh"
        merge_settings_from_reference \
            "$CLAUDE_DIR/settings.json" \
            "$CLAUDE_DIR/config/settings.reference.json"
        log_success "settings.json configured from reference"
    fi

    # Also ensure settings.local.json exists (machine-local config)
    configure_settings_local_json
}

# Register the `ours` merge driver that .gitattributes names for the
# regenerated substrate (data/genome-shared.json and friends). git does not
# build it in, so without this a pull or stash pop that meets a local rewrite
# stops with conflict markers inside JSON. Called before every pull so an
# existing clone gets it on its next update. Idempotent. The `.git` check
# keeps the write inside a repository root, never a parent repository.
# Details: docs/substrate-merge-strategy.md.
register_merge_drivers() {
    local repo="$1"
    [[ -e "$repo/.git" ]] || return 0
    git -C "$repo" config merge.ours.name "keep the side being merged into (regenerated substrate)"
    git -C "$repo" config merge.ours.driver true
}

install_git_hooks() {
    local git_dir="$CLAUDE_DIR/.git"
    local hook_src="$CLAUDE_DIR/config/git-hooks/post-merge"
    local hook_dst="$git_dir/hooks/post-merge"

    [[ -d "$git_dir/hooks" ]] || return 0
    [[ -f "$hook_src" ]] || return 0

    if [[ ! -f "$hook_dst" ]]; then
        cp "$hook_src" "$hook_dst"
        chmod +x "$hook_dst"
        log_success "Installed post-merge hook"
    elif ! diff -q "$hook_src" "$hook_dst" &>/dev/null; then
        log_info "Post-merge hook differs from template -- skipping (manual merge needed)"
    fi
}

configure_settings_local_json() {
    local reference="$CLAUDE_DIR/config/settings.local.reference.json"
    local target="$CLAUDE_DIR/settings.local.json"

    if [[ ! -f "$reference" ]]; then
        log_warn "settings.local.reference.json not found, skipping"
        return 0
    fi

    if [[ -f "$target" ]]; then
        log_info "settings.local.json exists, preserving (machine-local config)"
        return 0
    fi

    # Fresh install: copy reference with placeholder operator
    # /activate-grip wizard will replace __OPERATOR__ with user's signature
    log_info "Creating settings.local.json from reference template..."
    cp "$reference" "$target"
    log_success "settings.local.json created (operator set during /activate-grip)"
}

# ============================================================================
# MERGE INSTALLATION (Scenario B)
# ============================================================================

merge_install() {
    local source_dir="$1"
    local mode="${2:-symlink}"  # symlink or copy

    # Empty or "/" roots turn "$root/$dir" into a system path: called with no
    # arguments this loop once ran `ln -sfn /bin /bin` and broke the box.
    if ! grip_link_roots_are_sane "$source_dir" "$CLAUDE_DIR"; then
        log_error "Refusing to merge: unsafe source ($source_dir) or target ($CLAUDE_DIR) root"
        return 1
    fi

    log_info "Merging GRIP into existing ~/.claude..."
    log_info "Mode: $mode"

    # Track resolution statistics
    local kept_local=0
    local used_repo=0
    local added_new=0

    # Process each core directory
    for dir in "${CORE_DIRS[@]}"; do
        if [[ -d "$source_dir/$dir" ]]; then
            if [[ -d "$CLAUDE_DIR/$dir" ]] && [[ ! -L "$CLAUDE_DIR/$dir" ]]; then
                # Directory exists and is not a symlink - need to merge
                log_info "Merging directory: $dir/"
                merge_directory "$source_dir/$dir" "$CLAUDE_DIR/$dir" "$mode"
            else
                # Directory doesn't exist or is already a symlink - safe to link/copy
                if [[ "$mode" == "symlink" ]]; then
                    ln -sfn "$source_dir/$dir" "$CLAUDE_DIR/$dir"
                else
                    # Reached only when the target is absent or already a
                    # symlink, so the guard allows it either way (both count
                    # zero files). Applied regardless: keeping one unguarded
                    # deletion as a documented exception invites the next
                    # reader to add a second, and the branch conditions above
                    # could change without this line being revisited.
                    if ! grip_source_outweighs_target "$source_dir/$dir" "$CLAUDE_DIR/$dir"; then
                        log_warn "Skipped: $dir/ (source has ${GRIP_REPLACE_SRC_N} file(s) vs ${GRIP_REPLACE_TGT_N} installed - refusing to rm -rf)"
                        continue
                    fi
                    rm -rf "$CLAUDE_DIR/$dir"
                    cp -r "$source_dir/$dir" "$CLAUDE_DIR/$dir"
                fi
                log_success "Added: $dir/"
                added_new=$((added_new + 1))
            fi
        fi
    done

    # Process each core file
    for file in "${CORE_FILES[@]}"; do
        if [[ -f "$source_dir/$file" ]]; then
            if [[ -f "$CLAUDE_DIR/$file" ]] && [[ ! -L "$CLAUDE_DIR/$file" ]]; then
                # File exists - check if different
                if ! diff -q "$CLAUDE_DIR/$file" "$source_dir/$file" &>/dev/null; then
                    local resolution
                    resolution=$(ask_conflict_resolution "$file" "$CLAUDE_DIR/$file" "$source_dir/$file")

                    case "$resolution" in
                        keep-local)
                            log_info "Kept local: $file"
                            kept_local=$((kept_local + 1))
                            ;;
                        use-repo)
                            if [[ "$mode" == "symlink" ]]; then
                                ln -sf "$source_dir/$file" "$CLAUDE_DIR/$file"
                            else
                                cp "$source_dir/$file" "$CLAUDE_DIR/$file"
                            fi
                            log_success "Updated: $file"
                            used_repo=$((used_repo + 1))
                            ;;
                        merge-manual)
                            ${EDITOR:-vim} "$CLAUDE_DIR/$file"
                            log_info "Manually merged: $file"
                            ;;
                    esac
                else
                    log_info "Identical: $file"
                fi
            else
                # File doesn't exist - safe to link/copy
                if [[ "$mode" == "symlink" ]]; then
                    ln -sf "$source_dir/$file" "$CLAUDE_DIR/$file"
                else
                    cp "$source_dir/$file" "$CLAUDE_DIR/$file"
                fi
                log_success "Added: $file"
                added_new=$((added_new + 1))
            fi
        fi
    done

    # Create user files from templates
    create_user_files

    # GRIP's settings block, merged into the user's own settings.json (their
    # keys stay), as every other install path does. Without it verification
    # finds no hooks and stops the install before the venv is built, which is
    # what happened to anyone who used Claude Code before GRIP (GRIP Suite W2,
    # measured 2026-11-24).
    configure_settings_json
    install_git_hooks

    # Summary
    echo ""
    echo "Merge Summary"
    echo "============="
    echo "  Added new:    $added_new"
    echo "  Kept local:   $kept_local"
    echo "  Used repo:    $used_repo"

    log_success "Merge complete."
}

merge_directory() {
    local source_dir="$1"
    local target_dir="$2"
    local mode="$3"

    # For skill/agent directories, check file-level differences
    for source_file in "$source_dir"/*; do
        [[ -e "$source_file" ]] || continue

        local filename
        filename=$(basename "$source_file")
        local target_file="$target_dir/$filename"

        if [[ -d "$source_file" ]]; then
            # Recurse into subdirectories
            if [[ -d "$target_file" ]]; then
                merge_directory "$source_file" "$target_file" "$mode"
            else
                if [[ "$mode" == "symlink" ]]; then
                    ln -sfn "$source_file" "$target_file"
                else
                    cp -r "$source_file" "$target_file"
                fi
            fi
        elif [[ -f "$source_file" ]]; then
            if [[ -f "$target_file" ]]; then
                # Check if files are different
                if ! diff -q "$source_file" "$target_file" &>/dev/null; then
                    local dir_name
                    dir_name=$(basename "$source_dir")
                    local resolution
                    resolution=$(ask_conflict_resolution "$dir_name/$filename" "$target_file" "$source_file")

                    case "$resolution" in
                        use-repo)
                            if [[ "$mode" == "symlink" ]]; then
                                ln -sf "$source_file" "$target_file"
                            else
                                cp "$source_file" "$target_file"
                            fi
                            ;;
                        keep-local)
                            # Do nothing
                            ;;
                        merge-manual)
                            ${EDITOR:-vim} "$target_file"
                            ;;
                    esac
                fi
            else
                # Target doesn't exist - copy/link
                if [[ "$mode" == "symlink" ]]; then
                    ln -sf "$source_file" "$target_file"
                else
                    cp "$source_file" "$target_file"
                fi
            fi
        fi
    done
}

# ============================================================================
# UPGRADE FUNCTIONS
# ============================================================================

upgrade_clone() {
    log_info "Upgrading GRIP (clone mode)..."

    cd "$CLAUDE_DIR"
    register_merge_drivers "$CLAUDE_DIR"

    # Check for uncommitted changes
    if ! git diff --quiet 2>/dev/null; then
        log_warn "You have uncommitted changes in ~/.claude"
        confirm="$(_reply "Stash changes and continue? [y/N]: " N)"
        if [[ "$confirm" =~ ^[Yy]$ ]]; then
            git stash
        else
            log_error "Upgrade cancelled."
            exit 1
        fi
    fi

    git pull --rebase

    # Regenerate settings.json with absolute paths (may have been deleted by pull)
    configure_settings_json
    install_git_hooks

    log_success "Upgrade complete."
}

upgrade_symlink() {
    local source_dir
    source_dir=$(cat "$CLAUDE_DIR/.grip-symlinked")

    log_info "Upgrading GRIP (symlink mode)..."
    log_info "Source: $source_dir"

    cd "$source_dir"
    register_merge_drivers "$source_dir"
    git pull --rebase

    # Refresh symlinks in case new directories were added
    log_info "Refreshing symlinks..."
    create_symlinks "$source_dir" "$CLAUDE_DIR"

    # Regenerate settings.json with absolute paths
    configure_settings_json
    install_git_hooks

    log_success "Upgrade complete."
}

upgrade_copy() {
    local source_dir
    source_dir=$(cat "$CLAUDE_DIR/.grip-copy-source" 2>/dev/null || echo "")

    if [[ -z "$source_dir" ]] || [[ ! -d "$source_dir" ]]; then
        log_error "Cannot find GRIP source repository."
        log_info "Run 'scripts/sync.sh' manually to sync from a known location."
        exit 1
    fi

    log_info "Upgrading GRIP (copy mode)..."
    log_info "Source: $source_dir"

    cd "$source_dir"
    register_merge_drivers "$source_dir"
    git pull --rebase

    # Copy updated files
    copy_core_files "$source_dir" "$CLAUDE_DIR"

    # Regenerate settings.json with absolute paths
    configure_settings_json
    install_git_hooks

    log_success "Upgrade complete."
}

# ============================================================================
# VERIFICATION
# ============================================================================

verify_settings_hooks() {
    local settings_file="$CLAUDE_DIR/settings.json"
    local reference_file="$CLAUDE_DIR/config/settings.reference.json"
    local registry_file="$CLAUDE_DIR/config/hooks.registry.json"
    local validator="$CLAUDE_DIR/lib/settings_validate.py"

    if [[ ! -f "$settings_file" ]]; then
        log_error "settings.json not found"
        return 1
    fi
    if ! command -v python3 &>/dev/null; then
        log_error "Python is required for canonical settings validation"
        return 1
    fi
    if [[ ! -f "$reference_file" || ! -f "$registry_file" || ! -f "$validator" ]]; then
        log_error "Canonical reference, registry, or validator is missing"
        return 1
    fi
    if ! python3 "$validator" \
        --settings "$settings_file" \
        --reference "$reference_file" \
        --registry "$registry_file" \
        --profile install; then
        log_error "settings.json failed canonical hook validation"
        return 1
    fi
    log_success "settings.json canonical hooks and managed fields verified"
    return 0
}

verify_git_crypt_status() {
    if ! command -v git-crypt &>/dev/null; then
        log_warn "git-crypt not installed"
        return 0
    fi

    local py
    py="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "")"
    if [[ -n "$py" ]]; then
        local _sentinel=""
        for _s in "facts/identity.md" "facts/team.md"; do
            if [[ -f "$CLAUDE_DIR/$_s" ]]; then
                _sentinel="$CLAUDE_DIR/$_s"
                break
            fi
        done
        if [[ -n "$_sentinel" ]]; then
            local _is_enc
            _is_enc=$("$py" -c "
import sys
try:
    with open('${_sentinel}', 'rb') as f:
        print('yes' if f.read(9) == b'\x00GITCRYPT' else 'no')
except Exception:
    print('unknown')
" 2>/dev/null)
            if [[ "$_is_enc" == "no" ]]; then
                log_success "git-crypt unlocked"
            else
                log_warn "git-crypt locked (run: git-crypt unlock)"
            fi
        fi
    fi

    return 0
}

verify_kono_installation() {
    local venv_python="$CLAUDE_DIR/venv/bin/python3"

    if [[ ! -x "$venv_python" ]]; then
        log_info "Python venv not found, KONO features disabled"
        return 0
    fi

    if "$venv_python" -c "import apsw, sqlite_vec" 2>/dev/null; then
        log_success "KONO Python packages installed"
    else
        log_info "KONO packages not installed (optional — run bootstrap-semantic-rl.sh)"
    fi

    if [[ -f "$CLAUDE_DIR/embeddings.db" ]]; then
        log_success "KONO embeddings.db exists"
    else
        log_info "KONO embeddings.db not found (optional — created on first KONO sync)"
    fi

    return 0
}

verify_installation() {
    # --defer-signing: a fresh install runs this before bootstrap_semantic_rl has
    # built the venv, so the signing probe would grade an interpreter that is not
    # GRIP's. main() probes signing itself once the venv exists (GRIP#9370).
    local defer_signing=0
    if [[ "${1:-}" == "--defer-signing" ]]; then
        defer_signing=1
    fi

    log_info "Verifying installation..."

    local errors=0

    # Check core directories
    for dir in "${CORE_DIRS[@]}"; do
        if [[ ! -d "$CLAUDE_DIR/$dir" ]] && [[ ! -L "$CLAUDE_DIR/$dir" ]]; then
            log_error "Missing: $dir/"
            errors=$((errors + 1))
        fi
    done

    # Check core files
    for file in "${CORE_FILES[@]}"; do
        if [[ ! -f "$CLAUDE_DIR/$file" ]] && [[ ! -L "$CLAUDE_DIR/$file" ]]; then
            log_error "Missing: $file"
            errors=$((errors + 1))
        fi
    done

    # Settings hooks validation
    if ! verify_settings_hooks; then
        errors=$((errors + 1))
    fi

    # git-crypt status
    verify_git_crypt_status || true

    # KONO check (optional)
    if [[ -d "$CLAUDE_DIR/venv" ]]; then
        verify_kono_installation || true
    fi

    # Signing capability. WARNS, never blocks -- see verify_signing_capability.
    local signing_degraded=0
    if [[ $defer_signing -eq 0 ]]; then
        verify_signing_capability || signing_degraded=1
    fi

    if [[ $errors -eq 0 ]]; then
        if [[ $defer_signing -eq 1 ]]; then
            log_success "Installation files verified; signing is checked once the Python environment is ready."
        elif [[ $signing_degraded -eq 1 ]]; then
            log_success "Installation verified, with 1 warning (signing degraded -- see above)."
        else
            log_success "Installation verified successfully."
        fi
        return 0
    else
        log_error "Installation verification failed with $errors errors."
        return 1
    fi
}

# Can this install actually produce a real decision-record seal?
#
# Origin: 2026-09-20 release-readiness audit, confirmed blocker B1. Two of the
# three documented install paths never installed requirements.txt, so
# `cryptography>=48` was absent and every record silently downgraded to a
# shared-key HMAC the project itself calls forgeable -- while this very function
# logged "Installation verified successfully." The success line was the defect:
# not that the check failed, but that there was no check and the message implied
# one.
#
# WARNS, NEVER BLOCKS, and the distinction is deliberate. Refusing the install on
# a missing capability would block `pip install cryptography`, which is the
# remedy -- the deadlock rules/gate-resolution-escape-hatch.md Invariant 1
# forbids, and the same split bin/grip-py already makes (refuse a system
# interpreter, warn on capability).
#
# The probe CALLS lib/pq_reexec.can_dual_sign() rather than copying its import
# string, so there is one definition of "can dual-sign" and not two that can drift.
verify_signing_capability() {
    local venv_python
    venv_python="$(resolve_venv_python "$CLAUDE_DIR/venv" 2>/dev/null)" || venv_python=""

    # resolve_venv_python falls back to a system interpreter when the venv has
    # none. Probing that one graded the wrong Python and told a stranger to
    # pip-install into Homebrew's (GRIP#9370), so only the venv's own counts.
    if [[ "$venv_python" != "$CLAUDE_DIR/venv/"* ]] || [[ ! -x "$venv_python" ]]; then
        log_warn "No GRIP Python environment at $CLAUDE_DIR/venv: cannot confirm decision records will be signed."
        log_warn "  Fix: bash $CLAUDE_DIR/scripts/bootstrap-semantic-rl.sh"
        return 1
    fi

    if "$venv_python" -c "import sys; sys.path.insert(0, '$CLAUDE_DIR/lib'); from pq_reexec import can_dual_sign; raise SystemExit(0 if can_dual_sign() else 1)" 2>/dev/null; then
        log_success "Signing capability confirmed (Ed25519 + ML-DSA-65)."
        return 0
    fi

    log_warn "SIGNING DEGRADED: this install cannot dual-sign."
    log_warn "  Decision records will carry custody: demo-shared-key, which is forgeable."
    log_warn "  Fix: \"$venv_python\" -m pip install --prefer-binary '$GRIP_SIGNING_REQUIREMENT'"
    return 1
}

# ============================================================================
# POST-INSTALL VALIDATION (Gen 316)
# ============================================================================

post_install_validation() {
    log_info "Running post-install validation..."

    local warnings=0
    local fixed=0

    # 1. Check for broken symlinks (symlink mode only)
    if [[ -f "$CLAUDE_DIR/.grip-symlinked" ]]; then
        local source_dir
        source_dir=$(cat "$CLAUDE_DIR/.grip-symlinked" 2>/dev/null)

        # An empty marker would make "$source_dir/$dir" read "/bin" and "/lib".
        if ! grip_link_roots_are_sane "$source_dir" "$CLAUDE_DIR"; then
            log_warn "Not repairing symlinks: .grip-symlinked names no usable source ($source_dir)"
            source_dir=""
            warnings=$((warnings + 1))
        fi

        for dir in "${CORE_DIRS[@]}"; do
            if [[ -L "$CLAUDE_DIR/$dir" ]] && [[ ! -e "$CLAUDE_DIR/$dir" ]]; then
                log_warn "Broken symlink detected: $dir/"
                # Attempt to fix
                if [[ -n "$source_dir" ]] && [[ -d "$source_dir/$dir" ]]; then
                    ln -sfn "$source_dir/$dir" "$CLAUDE_DIR/$dir"
                    log_success "Fixed symlink: $dir/"
                    fixed=$((fixed + 1))
                else
                    warnings=$((warnings + 1))
                fi
            fi
        done
    fi

    # 2. Fix hook permissions
    local hook_files=(
        "$CLAUDE_DIR/hooks/session-start.sh"
        "$CLAUDE_DIR/hooks/context-gate-hook.py"
    )

    for hook in "${hook_files[@]}"; do
        if [[ -f "$hook" ]]; then
            local ext="${hook##*.}"
            if [[ "$ext" == "sh" ]] && [[ ! -x "$hook" ]]; then
                chmod +x "$hook"
                log_success "Fixed permissions: $(basename "$hook")"
                fixed=$((fixed + 1))
            elif [[ "$ext" == "py" ]] && [[ ! -r "$hook" ]]; then
                chmod +r "$hook"
                log_success "Fixed permissions: $(basename "$hook")"
                fixed=$((fixed + 1))
            fi
        fi
    done

    # Fix script permissions. Only a command (a file starting with #!) is made
    # executable: a mode change on anything else made the clone read as dirty, so
    # a second install refused its own upgrade (GRIP Suite W2, measured 2026-11-24).
    if [[ -d "$CLAUDE_DIR/scripts" ]]; then
        for script in "$CLAUDE_DIR/scripts"/*.sh; do
            [[ -f "$script" ]] || continue
            if [[ ! -x "$script" ]] && [[ "$(head -c 2 "$script")" == "#!" ]]; then
                chmod +x "$script"
                fixed=$((fixed + 1))
            fi
        done
    fi

    # Fix bin permissions, for commands only (see above): bin/ also holds Python
    # modules and a PowerShell file, which are not run directly here.
    if [[ -d "$CLAUDE_DIR/bin" ]]; then
        for bin in "$CLAUDE_DIR/bin"/*; do
            [[ -f "$bin" ]] || continue
            if [[ ! -x "$bin" ]] && [[ "$(head -c 2 "$bin")" == "#!" ]]; then
                chmod +x "$bin"
                fixed=$((fixed + 1))
            fi
        done
    fi

    # 3. Fix hardcoded absolute paths in settings.json (pre-reference installs)
    if [[ -f "$CLAUDE_DIR/settings.json" ]]; then
        if grep -qE '/Users/|/home/|C:\\\\|C:/' "$CLAUDE_DIR/settings.json" 2>/dev/null; then
            log_warn "settings.json contains hardcoded paths, regenerating from reference..."
            configure_settings_json
            fixed=$((fixed + 1))
        fi
    else
        log_info "Creating settings.json from reference..."
        configure_settings_json
        fixed=$((fixed + 1))
    fi

    # Also check settings.local.json
    if [[ -f "$CLAUDE_DIR/settings.local.json" ]]; then
        if grep -qE '/Users/|/home/|C:\\\\|C:/' "$CLAUDE_DIR/settings.local.json" 2>/dev/null; then
            log_warn "settings.local.json contains hardcoded paths"
            log_warn "Manual fix required for settings.local.json"
            warnings=$((warnings + 1))
        fi
    fi

    # 4. Ensure runtime directories exist
    for dir in "${RUNTIME_DIRS[@]}"; do
        if [[ ! -d "$CLAUDE_DIR/$dir" ]]; then
            mkdir -p "$CLAUDE_DIR/$dir"
        fi
    done

    # Summary
    if [[ $fixed -gt 0 ]]; then
        log_success "Fixed $fixed issue(s)"
    fi

    if [[ $warnings -gt 0 ]]; then
        log_warn "$warnings warning(s) - see above"
    fi

    return 0
}

# ============================================================================
# KONO SCHEMA INITIALIZATION (Gen 315)
# ============================================================================

init_kono_schema() {
    log_info "Initializing KONO schema..."

    local venv_python
    venv_python="$(resolve_venv_python "$CLAUDE_DIR/venv")" || {
        log_warn "No Python found, skipping KONO init"
        return 0
    }

    # Skip if kono_schema.py doesn't exist
    if [[ ! -f "$CLAUDE_DIR/lib/kono_schema.py" ]]; then
        log_warn "kono_schema.py not found, skipping KONO init"
        return 0
    fi

    # Initialize KONO schema
    if "$venv_python" "$CLAUDE_DIR/lib/kono_schema.py" init 2>/dev/null; then
        log_success "KONO schema initialized"
    else
        log_warn "KONO schema init failed (may need embeddings.db first)"
    fi

    return 0
}

# ============================================================================
# PATH SETUP (Gen 319)
# ============================================================================

setup_shell_path() {
    local path_line='export PATH="$HOME/.claude/bin:$PATH"'
    local marker="# GRIP PATH"

    log_info "Configuring shell PATH..."

    # Zsh
    if [[ -f "$HOME/.zshrc" ]]; then
        if ! grep -q "$marker" "$HOME/.zshrc" 2>/dev/null; then
            echo "" >> "$HOME/.zshrc"
            echo "$marker" >> "$HOME/.zshrc"
            echo "$path_line" >> "$HOME/.zshrc"
            log_success "Added GRIP to PATH in ~/.zshrc"
        else
            log_info "GRIP PATH already in ~/.zshrc"
        fi
    fi

    # Bash - .bashrc
    if [[ -f "$HOME/.bashrc" ]]; then
        if ! grep -q "$marker" "$HOME/.bashrc" 2>/dev/null; then
            echo "" >> "$HOME/.bashrc"
            echo "$marker" >> "$HOME/.bashrc"
            echo "$path_line" >> "$HOME/.bashrc"
            log_success "Added GRIP to PATH in ~/.bashrc"
        else
            log_info "GRIP PATH already in ~/.bashrc"
        fi
    fi

    # Bash - .bash_profile (macOS uses this)
    if [[ -f "$HOME/.bash_profile" ]]; then
        if ! grep -q "$marker" "$HOME/.bash_profile" 2>/dev/null; then
            echo "" >> "$HOME/.bash_profile"
            echo "$marker" >> "$HOME/.bash_profile"
            echo "$path_line" >> "$HOME/.bash_profile"
            log_success "Added GRIP to PATH in ~/.bash_profile"
        else
            log_info "GRIP PATH already in ~/.bash_profile"
        fi
    fi

    # If no shell config exists, create .bashrc
    if [[ ! -f "$HOME/.zshrc" && ! -f "$HOME/.bashrc" && ! -f "$HOME/.bash_profile" ]]; then
        echo "$marker" >> "$HOME/.bashrc"
        echo "$path_line" >> "$HOME/.bashrc"
        log_success "Created ~/.bashrc with GRIP PATH"
    fi
}

# ============================================================================
# SHELL FUNCTIONS + gg++ ALIAS (Gen 497)
# ============================================================================

setup_shell_functions() {
    local source_line='source "$HOME/.claude/lib/shell-functions.sh"'
    local alias_line='alias gg++="gogrip"'
    local marker="# GRIP SHELL FUNCTIONS"

    log_info "Configuring GRIP shell functions and gg++ alias..."

    for rcfile in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile"; do
        if [[ -f "$rcfile" ]]; then
            if ! grep -q "$marker" "$rcfile" 2>/dev/null; then
                {
                    echo ""
                    echo "$marker"
                    echo "$source_line"
                    echo "$alias_line"
                } >> "$rcfile"
                log_success "Added GRIP shell functions to $(basename "$rcfile")"
            else
                log_info "GRIP shell functions already in $(basename "$rcfile")"
            fi
        fi
    done
}

# ============================================================================
# NODE CONFLICT DETECTION (Gen 319)
# ============================================================================

detect_node_conflicts() {
    local conflicts_found=false

    log_info "Checking for installation conflicts..."

    # Check for npm global claude-code installation (deprecated)
    if command -v npm &>/dev/null; then
        if npm list -g @anthropic-ai/claude-code 2>/dev/null | grep -q "claude-code"; then
            echo ""
            log_warn "Found npm global install: @anthropic-ai/claude-code"
            echo "       This installation method is deprecated and may conflict with GRIP."
            echo "       Consider removing: npm uninstall -g @anthropic-ai/claude-code"
            conflicts_found=true
        fi
    fi

    # Check for multiple 'claude' binaries in PATH
    local claude_paths
    claude_paths=$(which -a claude 2>/dev/null | sort -u || true)
    local claude_count
    # grep -c prints 0 AND exits 1 when nothing matches; `|| echo "0"` added a
    # second 0, and on a machine with no claude the comparison below printed
    # "syntax error in expression" (W1 Linux stranger rehearsal, 2026-11-24).
    claude_count=$(printf '%s\n' "$claude_paths" | grep -c . || true)
    if [[ $claude_count -gt 1 ]]; then
        echo ""
        log_warn "Multiple 'claude' binaries found in PATH:"
        echo "$claude_paths" | while read -r path; do echo "       - $path"; done
        echo "       This may cause unexpected behavior."
        conflicts_found=true
    fi

    # Check for Homebrew-installed claude (cask)
    if command -v brew &>/dev/null; then
        if brew list --cask 2>/dev/null | grep -q "^claude"; then
            # This is fine - just informational
            log_info "Found Homebrew cask: claude-code (this is OK)"
        fi
        # Check for formula (unlikely but possible conflict)
        if brew list --formula 2>/dev/null | grep -q "^claude"; then
            echo ""
            log_warn "Found Homebrew formula install of claude"
            echo "       Consider: brew uninstall claude"
            conflicts_found=true
        fi
    fi

    # Check for old MCP server global installs (deprecated method)
    if command -v npm &>/dev/null; then
        local old_mcps
        old_mcps=$(npm list -g 2>/dev/null | grep "@modelcontextprotocol/server-" || true)
        if [[ -n "$old_mcps" ]]; then
            echo ""
            log_warn "Found deprecated npm global MCP servers:"
            echo "$old_mcps" | while read -r line; do echo "       $line"; done
            echo "       New method: claude mcp add <server>"
            conflicts_found=true
        fi
    fi

    if [[ "$conflicts_found" == "true" ]]; then
        echo ""
        log_warn "Conflicts detected. GRIP will still work, but you may want to clean up."
        log_info "Run installer with --fix-conflicts to auto-remove (future feature)"
    else
        log_success "No installation conflicts detected"
    fi
}

# ============================================================================
# CLAUDE CODE CLI INSTALLER
# ============================================================================

ensure_claude_code() {
    if [[ "${SKIP_CLAUDE_INSTALL:-false}" == "true" ]]; then
        log_info "Skipping Claude Code CLI install (--no-claude-install)"
        return 0
    fi
    if command -v claude &>/dev/null; then
        local version
        version=$(claude --version 2>/dev/null || echo "unknown")
        log_success "Claude Code CLI found: $version"
        return 0
    fi

    log_info "Claude Code CLI not found. Installing..."
    case "$PLATFORM" in
        macos|linux)
            if curl -fsSL https://claude.ai/install.sh | bash; then
                log_success "Claude Code CLI installed"
            else
                log_warn "Claude Code CLI installation failed"
                log_info "Install manually: curl -fsSL https://claude.ai/install.sh | bash"
            fi
            ;;
        windows)
            # Claude Code's own Windows installer is PowerShell. It picks its build from
            # PROCESSOR_ARCHITECTURE, which an x64 Git Bash on Windows for Arm passes down as
            # AMD64 (Codex on GRIP#9888), so the machine-wide value is set first, and on Arm
            # it installs the native win32-arm64 build. A failure only warns, as on macOS and
            # Linux, because GRIP itself installs without Claude Code.
            if powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "\$env:PROCESSOR_ARCHITECTURE = [Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITECTURE', 'Machine'); irm https://claude.ai/install.ps1 | iex"; then
                log_success "Claude Code CLI installed"
            else
                log_warn "Claude Code CLI installation failed"
                log_info "Install manually in PowerShell: irm https://claude.ai/install.ps1 | iex"
            fi
            ;;
    esac

    if command -v claude &>/dev/null; then
        log_success "Claude Code CLI installed successfully"
    else
        log_warn "Claude Code CLI installation may need a shell restart"
    fi
}

# ============================================================================
# RTK (TOKEN-COMPRESSION PROXY) INSTALLER
# ============================================================================
# RTK (https://www.rtk-ai.app/) is the CLI token-compression proxy wired as the
# last PreToolUse Bash hook (lib/rtk-rg-passthrough.sh). NON-FATAL: that hook
# degrades gracefully when rtk is absent (emits {} = no rewrite), so a failed or
# declined install never breaks a single Bash tool call. Idempotent — skips when
# rtk is already on PATH. Origin: blackbook 2026-06-24 (rtk was unwired here).

ensure_rtk() {
    if command -v rtk &>/dev/null; then
        log_success "RTK found: $(rtk --version 2>/dev/null || echo installed)"
        return 0
    fi

    log_info "RTK (token-compression proxy) not found. Installing..."
    case "$PLATFORM" in
        macos)
            if command -v brew &>/dev/null; then
                brew install rtk || log_warn "brew install rtk failed (non-fatal)"
            else
                log_warn "Homebrew not found — install rtk with: brew install rtk"
            fi
            ;;
        linux)
            if command -v brew &>/dev/null; then
                brew install rtk || log_warn "brew install rtk failed (non-fatal)"
            elif command -v cargo &>/dev/null; then
                cargo install rtk || log_warn "cargo install rtk failed (non-fatal)"
            else
                log_warn "No brew/cargo — install rtk manually: https://www.rtk-ai.app/"
            fi
            ;;
        windows)
            log_warn "On Windows, install rtk manually: cargo install rtk"
            ;;
    esac

    if command -v rtk &>/dev/null; then
        log_success "RTK installed: $(rtk --version 2>/dev/null || echo ok)"
    else
        log_warn "RTK not installed — rtk-rg-passthrough hook degrades gracefully (no token compression until rtk is on PATH)"
    fi
}

# cloudflared is required by grip-cos (the Chief-of-Staff iPhone-shortcut tunnel).
# Without it `grip-cos up` fails at runtime on a fresh machine. Delegates to the
# canonical shim lib/ensure_cloudflared.py — auto-installs on macOS+Homebrew,
# prints a platform-correct hint elsewhere. Non-fatal: grip-cos surfaces the same
# hint at runtime if it is still missing. Idempotent (skips when already present).
ensure_cloudflared() {
    local py
    local helper
    py="$(command -v python3 || command -v python || echo "")"
    if [ -z "$py" ]; then
        log_warn "python3 not found — skipping cloudflared check (needed by grip-cos)"
        return 0
    fi
    helper="$CLAUDE_DIR/lib/ensure_cloudflared.py"
    if [[ ! -f "$helper" ]]; then
        helper="$SCRIPT_DIR/../lib/ensure_cloudflared.py"
    fi
    if "$py" "$helper"; then
        log_success "cloudflared ready (grip-cos iPhone-shortcut tunnel)"
    else
        log_warn "cloudflared not installed — grip-cos will need it; see the hint above"
    fi
}

# ============================================================================
# UPDATE MODE
# ============================================================================

run_update() {
    log_info "Updating GRIP..."

    local mode
    # Set true only by the clone-mode step below, when it has already
    # composed settings.json against HEAD under the plan-v4 ownership
    # policy -- so the tail call skips the older two-way reference merge
    # for settings.json specifically, instead of re-merging on top of a
    # result that merge already matches (review finding F5 on PR #8719:
    # running both unconditionally re-diverges the file the first one just
    # composed, which is exactly why the C6 test stubs configure_settings_json
    # out rather than letting it run for real).
    local settings_composed=false
    if [[ -d "$CLAUDE_DIR/.git" ]]; then
        mode="clone"
    elif [[ -f "$CLAUDE_DIR/.grip-symlinked" ]]; then
        mode="symlink"
    elif [[ -f "$CLAUDE_DIR/.grip-copy-source" ]]; then
        mode="copy"
    else
        log_error "Cannot determine installation mode."
        log_info "Run 'scripts/install.sh' for fresh installation."
        exit 1
    fi

    case "$mode" in
        clone)
            cd "$CLAUDE_DIR"
            register_merge_drivers "$CLAUDE_DIR"
            # plan-v4 C6: settings.json is excluded from the stash below, not
            # merely stashed-and-restored with it -- a stash pop can conflict
            # on it like any other tracked file, and that conflict is exactly
            # what this PR exists to stop happening. Snapshotting it and
            # restoring it to HEAD's committed form BEFORE the dirty-tree
            # check means `git diff --quiet` and `git stash` never see it at
            # all, so the pop below can no longer touch it either way.
            local settings_snapshot="" settings_old_head=""
            if [[ -f "$CLAUDE_DIR/settings.json" ]]; then
                settings_old_head="$(git rev-parse HEAD 2>/dev/null || true)"
                settings_snapshot="$(mktemp "${TMPDIR:-/tmp}/grip-settings-snapshot.XXXXXX")"
                cp "$CLAUDE_DIR/settings.json" "$settings_snapshot"
                git checkout -- settings.json 2>/dev/null || true
            fi
            local stashed=false
            if ! git diff --quiet 2>/dev/null; then
                log_warn "Uncommitted changes detected. Stashing..."
                git stash
                stashed=true
            fi
            if git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
                # settings.json was already reset to HEAD above and is no
                # longer protected by the stash below (that is the point of
                # excluding it) -- so unlike every other file touched by
                # this update, a failed pull here would otherwise discard
                # its pre-pull content for good: `set -e` kills the script
                # at the next failing command, before the restore further
                # down ever runs, and the snapshot is left behind as an
                # orphaned file in $TMPDIR nothing points back to (review
                # finding F4 on PR #8719, reproduced against an unreachable
                # origin). Restore it here, in the one place that still
                # knows where the snapshot is, before giving up the same way
                # this function always has on a failed pull.
                if ! git pull --rebase; then
                    if [[ -n "$settings_snapshot" ]]; then
                        cp "$settings_snapshot" "$CLAUDE_DIR/settings.json"
                        rm -f "$settings_snapshot"
                    fi
                    log_error "git pull --rebase failed -- settings.json restored to its pre-pull content, update aborted"
                    exit 1
                fi
            else
                log_warn "Current branch '$(git branch --show-current)' has no upstream tracking branch — skipping git sync"
            fi
            if [[ "$stashed" == "true" ]]; then
                log_info "Restoring stashed local changes..."
                git stash pop
            fi
            # The pull's own checkout just overwrote settings.json with the
            # NEW tracked copy; compose it with the pre-pull snapshot under
            # the same ownership policy tree_sync_resolve.py uses, so a local
            # edit the pull never touched survives it. On a refusal (C4: a
            # managed hook group missing, or a hook script that is no longer
            # there), restore the snapshot rather than leave the pull's bare
            # HEAD checkout in place -- the comment this replaces claimed
            # that was "this function's pre-existing behaviour", which
            # review finding F4 on PR #8719 showed false: before settings.json
            # was carved out of the stash, a failed merge of this kind left
            # the STASHED original (permissions, model, enabledPlugins and
            # every other USER_PRESERVED_KEYS value intact), not a bare
            # reference-less HEAD file. Restoring here puts the fallback
            # two-way merge below back on the operator's own file, matching
            # that original behaviour instead of a new, narrower one.
            if [[ -n "$settings_snapshot" && -n "$settings_old_head" ]]; then
                if "$CLAUDE_DIR/bin/grip-settings-reconcile" --claude-dir "$CLAUDE_DIR" --after-pull \
                    --old-head "$settings_old_head" --snapshot "$settings_snapshot"; then
                    log_success "settings.json composed against the pull"
                    settings_composed=true
                else
                    log_warn "settings.json composition after pull failed -- restoring the pre-pull copy for the reference merge below"
                    cp "$settings_snapshot" "$CLAUDE_DIR/settings.json"
                fi
                rm -f "$settings_snapshot"
            fi
            ;;
        symlink)
            local source_dir
            source_dir=$(cat "$CLAUDE_DIR/.grip-symlinked")
            cd "$source_dir"
            register_merge_drivers "$source_dir"
            git pull --rebase
            create_symlinks "$source_dir" "$CLAUDE_DIR"
            ;;
        copy)
            local source_dir
            source_dir=$(cat "$CLAUDE_DIR/.grip-copy-source" 2>/dev/null)
            if [[ -z "$source_dir" ]] || [[ ! -d "$source_dir" ]]; then
                log_error "Source repository not found."
                exit 1
            fi
            cd "$source_dir"
            register_merge_drivers "$source_dir"
            git pull --rebase
            copy_core_files "$source_dir" "$CLAUDE_DIR"
            ;;
    esac

    configure_settings_json "$settings_composed"
    install_git_hooks
    offer_launchd_agents
    log_success "GRIP updated"
}

# ============================================================================
# MAIN
# ============================================================================

main() {
    # Handle --update and --verify actions
    if [[ "$INSTALL_ACTION" == "update" ]]; then
        detect_platform
        run_update
        ensure_rtk
        ensure_cloudflared
        verify_installation
        post_install_validation
        bootstrap_genome_and_cube
        bootstrap_platform_paths
        log_success "GRIP update complete"
        return 0
    fi

    if [[ "$INSTALL_ACTION" == "launchd-agents" ]]; then
        detect_platform
        if [[ "$PLATFORM" != "macos" ]]; then
            log_info "Launchd agents are macOS only; nothing to write on $PLATFORM."
            return 0
        fi
        install_launchd_agents
        return 0
    fi

    if [[ "$INSTALL_ACTION" == "verify" ]]; then
        detect_platform
        verify_installation
        verify_settings_hooks
        verify_git_crypt_status
        if [[ -d "$CLAUDE_DIR/venv" ]]; then
            verify_kono_installation
        fi
        # Run the comprehensive verification script
        if [[ -x "$SCRIPT_DIR/verify-install.sh" ]]; then
            echo ""
            "$SCRIPT_DIR/verify-install.sh"
        fi
        return 0
    fi

    echo ""
    echo "GRIP Installer v$GRIP_VERSION"
    echo "========================="
    echo ""

    detect_platform
    log_info "Platform: $PLATFORM (symlinks: $SYMLINK_AVAILABLE)"

    # Ensure Claude Code CLI is installed
    ensure_claude_code

    # Ensure RTK token-compression proxy is installed (non-fatal; hook degrades)
    ensure_rtk

    # Ensure cloudflared is installed (non-fatal) — grip-cos needs it for the iPhone shortcut
    ensure_cloudflared

    local scenario
    scenario=$(detect_scenario)
    log_info "Scenario: $scenario"
    echo ""

    case "$scenario" in
        fresh)
            local mode
            mode=$(ask_installation_mode)

            case "$mode" in
                clone-home)
                    install_fresh_clone
                    ;;
                symlink)
                    clone_dir="$(_reply "Clone location [$HOME/Projects/grip]: " "$HOME/Projects/grip")"
                    install_fresh_symlink "${clone_dir:-$HOME/Projects/grip}"
                    ;;
                copy)
                    clone_dir="$(_reply "Clone location [$HOME/Projects/grip]: " "$HOME/Projects/grip")"
                    install_fresh_copy "${clone_dir:-$HOME/Projects/grip}"
                    ;;
            esac
            ;;

        merge)
            local strategy
            strategy=$(ask_merge_strategy)

            case "$strategy" in
                merge)
                    local backup_dir
                    backup_dir=$(backup_user_config)

                    local mode
                    mode=$(ask_installation_mode)

                    clone_dir="$(_reply "Clone location [$HOME/Projects/grip]: " "$HOME/Projects/grip")"
                    clone_dir="${clone_dir:-$HOME/Projects/grip}"

                    if [[ ! -d "$clone_dir" ]]; then
                        log_info "Cloning GRIP repository..."
                        mkdir -p "$(dirname "$clone_dir")"
                        git clone "$REPO_URL" "$clone_dir"
                    fi

                    # Create marker file
                    if [[ "$mode" == "symlink" ]]; then
                        echo "$clone_dir" > "$CLAUDE_DIR/.grip-symlinked"
                    else
                        echo "$clone_dir" > "$CLAUDE_DIR/.grip-copy-source"
                    fi

                    merge_install "$clone_dir" "$mode"
                    ;;
                replace)
                    log_warn "This will DELETE all files in ~/.claude"
                    confirm="$(_reply "Are you absolutely sure? [type 'yes']: " no)"
                    if [[ "$confirm" == "yes" ]]; then
                        rm -rf "$CLAUDE_DIR"

                        local mode
                        mode=$(ask_installation_mode)

                        case "$mode" in
                            clone-home)
                                install_fresh_clone
                                ;;
                            symlink)
                                clone_dir="$(_reply "Clone location [$HOME/Projects/grip]: " "$HOME/Projects/grip")"
                                install_fresh_symlink "${clone_dir:-$HOME/Projects/grip}"
                                ;;
                            copy)
                                clone_dir="$(_reply "Clone location [$HOME/Projects/grip]: " "$HOME/Projects/grip")"
                                install_fresh_copy "${clone_dir:-$HOME/Projects/grip}"
                                ;;
                        esac
                    else
                        log_info "Cancelled."
                        exit 0
                    fi
                    ;;
                abort)
                    log_info "Installation cancelled."
                    exit 0
                    ;;
            esac
            ;;

        upgrade-clone)
            upgrade_clone
            ;;

        upgrade-symlink)
            upgrade_symlink
            ;;

        upgrade-copy)
            upgrade_copy
            ;;

        upgrade-unknown)
            log_warn "Existing GRIP installation detected but mode unknown."
            log_info "To upgrade, run 'git pull' if ~/.claude is a repo,"
            log_info "or use 'scripts/sync.sh' to sync from a known source."
            ;;
    esac

    # Verify the installed files. The signing probe waits for the Python
    # environment that bootstrap_semantic_rl builds below (GRIP#9370).
    verify_installation --defer-signing

    # Post-install validation (Gen 316)
    post_install_validation

    # Bootstrap Semantic RL (venv, models, KONO)
    bootstrap_semantic_rl

    # Signing, now that the venv and its dependencies exist. Warns, never blocks.
    verify_signing_capability || true

    # Setup shell PATH (Gen 319)
    setup_shell_path

    # Setup shell functions and gg++ alias (Gen 497)
    setup_shell_functions

    # Optional launchd agents: named, never written (macOS only; GRIP#9370)
    offer_launchd_agents

    # Detect Node/Claude conflicts (Gen 319)
    detect_node_conflicts

    # Bootstrap Genome and CUBE, then platform paths (Windows only)
    bootstrap_genome_and_cube
    bootstrap_platform_paths

    echo ""
    echo "Next steps:"
    echo "  1. Reload your shell: source ~/.zshrc (or ~/.bashrc, or restart terminal)"
    echo "  2. Launch GRIP: gg++ (or: cd ~/.claude && claude)"
    echo "  3. Run the setup wizard: /activate-grip"
    echo "  4. Verify installation: ~/.claude/scripts/verify-install.sh"
    echo ""
    echo "Troubleshooting:"
    echo "  - If 'gg++' or 'grip' not found: check ~/.zshrc contains GRIP PATH and GRIP SHELL FUNCTIONS"
    echo "  - If 'claude' not found: curl -fsSL https://claude.ai/install.sh | bash"
    echo "  - Run 'claude doctor' to diagnose Claude Code issues"
    echo "  - See docs/CONTRIBUTING.md for the full troubleshooting guide"
}


# ============================================================================
# LAUNCHD AGENTS (macOS only) -- OPT-IN (GRIP#9370)
# config/**/*.plist.template are optional background agents. Several serve one
# operator's own relay, tunnel, WhatsApp watcher and team posts, and three of
# the templates say themselves that a fresh install leaves them alone. So an
# install or an update writes no plist: offer_launchd_agents names the command,
# and install_launchd_agents writes them only for `install.sh --launchd-agents`.
# Neither loads an agent; that stays an explicit launchctl step per agent.
# ============================================================================

_launchd_templates() {
    find "$CLAUDE_DIR/config" -name "*.plist.template" -print0 2>/dev/null
}

# One template, with the three placeholders this step can fill, to stdout.
_launchd_render() {
    sed \
        -e "s|__GRIP_HOME__|${CLAUDE_DIR}|g" \
        -e "s|__CLAUDE_DIR__|${CLAUDE_DIR}|g" \
        -e "s|__CLAUDE_BIN__|${2}|g" \
        "$1"
}

# The placeholders a rendered plist still holds, space-separated, from the file
# named or from stdin for "-". Empty when the plist is complete.
_launchd_leftovers() {
    grep -oE '__[A-Z][A-Z0-9_]*__' "$1" | sort -u | tr '\n' ' ' || true
}

offer_launchd_agents() {
    [[ "$PLATFORM" == "macos" ]] || return 0

    # Count only what --launchd-agents would write: not installed yet, and
    # complete once rendered (com.grip.cos has its own setup script), so the
    # line does not come back after every update once the user has opted in.
    local agents_dir="$HOME/Library/LaunchAgents" tmpl label pending=0
    while IFS= read -r -d '' tmpl; do
        label="$(basename "$tmpl" .plist.template)"
        if [[ -f "$agents_dir/${label}.plist" ]]; then
            continue
        fi
        if [[ -z "$(_launchd_render "$tmpl" python3 | _launchd_leftovers -)" ]]; then
            pending=$((pending + 1))
        fi
    done < <(_launchd_templates)

    if [[ $pending -gt 0 ]]; then
        log_info "Launchd: $pending optional background agent(s) not installed. To write their plists (nothing is loaded):"
        log_info "  bash $CLAUDE_DIR/scripts/install.sh --launchd-agents"
    fi
    return 0
}

# Substitutes __GRIP_HOME__ / __CLAUDE_DIR__ / __CLAUDE_BIN__ in every template
# and writes the plist to ~/Library/LaunchAgents/. A template that still holds a
# placeholder after that is not written: com.grip.cos needs __PYTHON__ from
# scripts/grip-cos-tunnel-setup.sh, and a plist naming __PYTHON__ as its program
# cannot run. Counters use $((n + 1)), because ((n++)) from 0 returns status 1,
# which ends the script under set -e on bash 4.1 and later.
install_launchd_agents() {
    [[ "$PLATFORM" == "macos" ]] || return 0

    local agents_dir="$HOME/Library/LaunchAgents"
    mkdir -p "$agents_dir"

    # Resolve __CLAUDE_BIN__: venv python, else system python3
    local claude_bin
    claude_bin="$(resolve_venv_python "$CLAUDE_DIR/venv")" \
        || claude_bin="$(command -v python3 2>/dev/null || echo "python3")"

    local count=0 skipped=0 tmpl label dest staged unfilled
    # Loop over ALL templates — no per-file enumeration that rots
    while IFS= read -r -d '' tmpl; do
        label="$(basename "$tmpl" .plist.template)"
        dest="$agents_dir/${label}.plist"

        # Do not overwrite a user-customised installed plist
        if [[ -f "$dest" ]]; then
            log_info "Launchd: $label — already installed, skipping (rm to regenerate)"
            skipped=$((skipped + 1))
            continue
        fi

        staged="$dest.grip-staged"
        _launchd_render "$tmpl" "$claude_bin" > "$staged"
        unfilled="$(_launchd_leftovers "$staged")"
        if [[ -n "$unfilled" ]]; then
            rm -f "$staged"
            log_warn "Launchd: $label not written: it needs its own setup step for ${unfilled% } (see the comment at the top of $tmpl)"
            continue
        fi
        mv "$staged" "$dest"
        log_success "Launchd: generated ${label}.plist"
        count=$((count + 1))
    done < <(_launchd_templates)

    if [[ $count -gt 0 ]]; then
        log_success "Launchd: $count agent plist(s) written to $agents_dir"
        log_info "Launchd: none is loaded. Load one with: launchctl load ~/Library/LaunchAgents/<label>.plist"
    fi
    if [[ $skipped -gt 0 ]]; then
        log_info "Launchd: $skipped plist(s) skipped (already installed)"
    fi
    return 0
}

# ============================================================================
# SEMANTIC RL BOOTSTRAP
# ============================================================================

bootstrap_semantic_rl() {
    log_info "Bootstrapping Semantic RL (venv, models, KONO)..."

    local bootstrap_script="$CLAUDE_DIR/scripts/bootstrap-semantic-rl.sh"

    if [[ ! -f "$bootstrap_script" ]]; then
        log_warn "bootstrap-semantic-rl.sh not found, skipping"
        return 0
    fi

    if [[ ! -x "$bootstrap_script" ]]; then
        chmod +x "$bootstrap_script"
    fi

    local rc=0
    "$bootstrap_script" || rc=$?
    # 3: no embedding server answered (bootstrap-semantic-rl.sh). Semantic memory is
    # optional, so this is not an error.
    if [[ $rc -eq 3 ]]; then
        log_info "Semantic memory is off until an embedding server runs (optional)"
        log_info "Then run: $bootstrap_script"
        return 0
    fi
    # 4: the checkout has no requirements-core.txt, so no core package was
    # installed and the session-start heal cannot install them either. Fail the
    # install instead of finishing with exit 0 (GRIP#9487). Any other failure
    # stays non-fatal: the heal retries a failed download at session start.
    if [[ $rc -eq 4 ]]; then
        log_error "This GRIP checkout is incomplete (no requirements-core.txt), so no core packages were installed"
        log_info "Re-clone GRIP or restore requirements-core.txt, then run the installer again"
        return 1
    fi
    if [[ $rc -ne 0 ]]; then
        log_warn "Semantic RL bootstrap had errors (non-fatal)"
        log_info "Run manually: $bootstrap_script"
        return 0
    fi

    log_success "Semantic RL bootstrapped"
}

# ============================================================================
# GENOME + CUBE BOOTSTRAP
# ============================================================================

bootstrap_genome_and_cube() {
    log_info "Bootstrapping Genome and CUBE..."

    local dna_script="$CLAUDE_DIR/lib/converge_dna.py"
    local cube_wire="$CLAUDE_DIR/lib/cube_session_wire.py"

    local py
    py="$(resolve_venv_python "$CLAUDE_DIR/venv")" || py="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "python3")"

    # Ensure data dirs exist for genome history/outcomes
    mkdir -p "$CLAUDE_DIR/data" "$CLAUDE_DIR/cache"

    # Create state directories (GRASP consolidated state management)
    for state_dir in "${STATE_DIRS[@]}"; do
        mkdir -p "$CLAUDE_DIR/$state_dir"
    done

    # Genome: bootstrap-or-load (idempotent — loads existing, creates if missing)
    if [[ -f "$dna_script" ]]; then
        if "$py" "$dna_script" bootstrap-or-load 2>&1; then
            log_success "Genome initialized"
        else
            log_warn "Genome bootstrap failed (non-fatal)"
        fi
    else
        log_warn "converge_dna.py not found, skipping genome"
    fi

    # CUBE: seed a fresh design cube unless a cube record is already there.
    # lib/cube_session_wire.py owns the record's format. The inline block this
    # replaces dumped a CubeState's repr as a JSON string, which the module could
    # not read back, then died on len() (GRIP#9370); --seed-cache also replaces
    # that string where an earlier install left one.
    if [[ -f "$cube_wire" ]]; then
        if "$py" "$cube_wire" --seed-cache "$CLAUDE_DIR/cache/cube-state.json" 2>&1; then
            log_success "CUBE ready"
        else
            log_warn "CUBE init failed (non-fatal)"
        fi
    else
        log_warn "cube_session_wire.py not found, skipping CUBE"
    fi
}

# ============================================================================
# CROSS-PLATFORM PATH SETUP
# ============================================================================

bootstrap_platform_paths() {
    local setup_script="$CLAUDE_DIR/scripts/grip-setup.py"

    if [[ -f "$setup_script" ]]; then
        # Only run the fixer if on Windows (macOS/Linux don't need it)
        if [[ "$PLATFORM" == "windows" ]]; then
            local py
            py="$(resolve_venv_python "$CLAUDE_DIR/venv")" || py="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "python3")"
            log_info "Fixing hook paths for Windows..."
            "$py" "$setup_script" --fix 2>&1 && log_success "Windows paths fixed" || log_warn "Path fix failed (non-fatal)"
        fi
    fi
}

# Only run main when executed directly (not sourced for testing)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]] || [[ "${GRIP_INSTALL_RUN_MAIN:-1}" == "1" ]]; then
    main "$@"
fi
