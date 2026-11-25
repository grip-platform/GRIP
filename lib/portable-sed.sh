#!/usr/bin/env bash
# Portable sed in-place edit (macOS BSD sed vs GNU sed)
# Usage: portable_sed_i 'PATTERN' FILE
portable_sed_i() {
    local pattern="$1"
    local file="$2"
    if sed --version 2>/dev/null | grep -q 'GNU'; then
        sed -i "$pattern" "$file"
    else
        sed -i '' "$pattern" "$file"
    fi
}
