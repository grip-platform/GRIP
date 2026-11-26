#!/usr/bin/env bash
#
# Path Encoding Library (Unified)
# Single source of truth for Claude Code's project path encoding
#
# FORMULA (the same set as lib/path_encoding.py, measured against the folders
# Claude Code itself makes):
#   - Replace every '/', '\', ':', '.' and ' ' with '-'
#   Example: ~/.claude -> -Users-you--claude
#
# CROSS-PLATFORM:
#   - macOS/Linux: ~/... -> -Users-you-...
#   - Windows Git Bash: /c~ -> c--Users-...
#   - Windows paths, such as a hook payload's cwd:
#     C:\Users\<name>\.grip or C:/Users/<name>/.grip -> C--Users-<name>--grip
#
# USAGE:
#   source ~/.claude/lib/path-encoding.sh
#   encoded=$(encode_project_path "~/.claude")
#   encoded=$(encode_current_path)
#
# VERSION: 1.1.0
# DATE: 2026-11-26
#

# Guard against multiple sourcing
[[ -n "${_PATH_ENCODING_LOADED:-}" ]] && return 0
readonly _PATH_ENCODING_LOADED=1

# Encode a filesystem path to Claude's project directory format
# Cross-platform: macOS, Linux, Windows Git Bash
# Input: ~/project -> -Users-you-project
encode_project_path() {
    local path="$1"

    # Cross-platform path normalisation for Windows Git Bash
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*)
            # Windows Git Bash: /c~ -> c--Users-...
            if [[ "$path" =~ ^/([a-zA-Z])/ ]]; then
                local drive="${BASH_REMATCH[1]}"
                path="${drive}--${path:3}"
            fi
            ;;
    esac

    # Universal: the five characters lib/path_encoding.py collapses become
    # dashes. Before GRIP#6821 '\' and ':' were kept, so a Windows path gave a
    # name no folder has. printf, not echo: zsh's echo reads the '\U' of
    # C:\Users as an escape.
    printf '%s\n' "$path" | tr '/\\:. ' '-----'
}

# Encode current working directory
encode_current_path() {
    encode_project_path "$(pwd)"
}

# Decode a Claude project directory name back to filesystem path (best effort)
# Note: Decoding is lossy - cannot distinguish . from / in original
decode_project_path() {
    local encoded="$1"
    echo "$encoded" | sed 's|^-|/|' | sed 's|-|/|g'
}
