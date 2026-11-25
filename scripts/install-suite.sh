#!/usr/bin/env bash
# install-suite.sh -- install the GRIP Suite (GRIP, HAL, HAPPI and GRASP) on macOS or Linux.
#
#   bash install-suite.sh [--dry-run]
#   curl -fsSL <url-of-this-file> | bash -s -- [--dry-run]
#
# It checks for Python 3.11 or newer and a working Git, and installs them with
# Homebrew, apt-get or dnf when they are missing. It clones GRIP into
# ~/Projects/grip, or $GRIP_SUITE_DIR, which is also where GRIP's own installer
# looks for its copy when it merges into an existing ~/.claude. Then it runs the
# suite installer from that checkout, `python3 -m lib.suite_install`, which
# installs GRIP, HAL, HAPPI and GRASP and proves each one answers. Run it again
# and the steps that already hold are skipped.
#
# Exit codes are the suite installer's: 0 everything holds, 1 something failed,
# 2 a dry run found work to do, 130 stopped with Ctrl-C.
#
# Environment: GRIP_SUITE_DIR (where GRIP is cloned) and GRIP_REPO_URL (where it
# is cloned from; GRIP's own installer reads it too).
#
# Every command is inside a function, and the last lines call main, so a
# download cut short runs nothing. Commands get their input from /dev/null:
# under `curl | bash` the script itself is on the input, and a command that read
# it would swallow the rest of the script.

suite_repo_url() { echo "${GRIP_REPO_URL:-https://github.com/grip-platform/GRIP.git}"; }
suite_dir() { echo "${GRIP_SUITE_DIR:-$HOME/Projects/grip}"; }
# The Homebrew formula a Mac without Python 3.11+ gets; its interpreter is named
# after it (python@3.13 installs python3.13), so the version is written once here.
brew_python() { echo "python@3.13"; }
say() { printf '%s\n' "$*"; }
problem() { printf 'GRIP Suite: %s\n' "$*" >&2; }

print_help() {
    say "Usage: bash install-suite.sh [--dry-run]"
    say ""
    say "Installs GRIP, HAL, HAPPI and GRASP, and proves each one answers."
    say "  --dry-run   check everything and install nothing"
    say ""
    say "Exit codes: 0 everything holds; 1 something failed; 2 a dry run found"
    say "work to do; 130 stopped with Ctrl-C."
}

# python_ok <interpreter>: true when it runs and is Python 3.11 or newer.
python_ok() {
    "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' </dev/null >/dev/null 2>&1
}

# find_python: print the first Python 3.11+ on PATH, or in Homebrew's prefix.
find_python() {
    local name path prefix formula
    for name in python3 python3.15 python3.14 python3.13 python3.12 python3.11; do
        path="$(command -v "$name" 2>/dev/null)" || continue
        if python_ok "$path"; then
            echo "$path"
            return 0
        fi
    done
    if command -v brew >/dev/null 2>&1; then
        prefix="$(brew --prefix 2>/dev/null </dev/null)"
        formula="$(brew_python)"
        for path in "$prefix/bin/python3" "$prefix/bin/python${formula#python@}"; do
            if [[ -x "$path" ]] && python_ok "$path"; then
                echo "$path"
                return 0
            fi
        done
    fi
    return 1
}

on_macos() { [[ "$(uname -s)" == "Darwin" ]]; }

# Without Apple's command line developer tools, /usr/bin/git does not run Git: it
# opens Apple's installer. So on a Mac this is checked before Git is ever called.
macos_tools_missing() { on_macos && ! xcode-select -p >/dev/null 2>&1; }

git_ok() {
    macos_tools_missing && return 1
    git --version </dev/null >/dev/null 2>&1
}

# as_root: print the prefix that runs a command as root (nothing for root itself).
as_root() {
    if [[ "$(id -u)" == "0" ]]; then
        echo ""
    elif command -v sudo >/dev/null 2>&1; then
        echo "sudo"
    else
        return 1
    fi
}

install_on_macos() {
    local formula
    formula="$(brew_python)"
    if macos_tools_missing; then
        problem "Git needs Apple's command line developer tools. Run: xcode-select --install"
        problem "Finish Apple's installer, then run this command again."
        return 1
    fi
    if ! command -v brew >/dev/null 2>&1; then
        problem "Python 3.11 or newer is missing, and Homebrew is not installed to add it."
        problem "Install Python from https://www.python.org/downloads/ and run this command again."
        return 1
    fi
    say "Installing Python with Homebrew: brew install $formula"
    brew install "$formula" </dev/null
}

install_on_linux() {
    local root
    if ! root="$(as_root)"; then
        problem "Python 3.11+ or Git is missing, and installing them needs root or sudo."
        return 1
    fi
    if command -v apt-get >/dev/null 2>&1; then
        say "Installing Python and Git: ${root:+$root }apt-get install -y python3 python3-venv git"
        $root env DEBIAN_FRONTEND=noninteractive apt-get update -qq </dev/null &&
            $root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
                python3 python3-venv git ca-certificates </dev/null
    elif command -v dnf >/dev/null 2>&1; then
        say "Installing Python and Git: ${root:+$root }dnf install -y python3 git"
        $root dnf install -y -q python3 git </dev/null
    else
        problem "Python 3.11+ or Git is missing, and this system has neither apt-get nor dnf."
        problem "Install Python 3.11 or newer and Git, then run this command again."
        return 1
    fi
}

# prerequisites <dry-run 0|1>: print the Python to use, installing it and Git first
# when they are missing. A dry run installs nothing and returns 2 instead.
prerequisites() {
    local python
    python="$(find_python)"
    if [[ -n "$python" ]] && git_ok; then
        echo "$python"
        return 0
    fi
    if [[ "$1" == "1" ]]; then
        problem "Dry run: Python 3.11+ or Git is missing, and a real run would install it."
        return 2
    fi
    local installer=install_on_linux
    on_macos && installer=install_on_macos
    if ! "$installer" >&2; then
        problem "Python and Git could not be installed; the reason is above."
        return 1
    fi
    python="$(find_python)"
    if [[ -z "$python" ]]; then
        problem "Python 3.11 or newer is still missing; this system's packages are older."
        problem "Install Python 3.11 or newer (python.org or pyenv), then run this command again."
        return 1
    fi
    if ! git_ok; then
        problem "Git still does not run after the install."
        return 1
    fi
    echo "$python"
}

# get_grip <dry-run 0|1>: clone GRIP, or update the clone already there. A folder
# that is not a Git clone is never touched. A dry run with no clone returns 2.
get_grip() {
    local dir url
    dir="$(suite_dir)"
    url="$(suite_repo_url)"
    if [[ -d "$dir/.git" ]]; then
        if [[ "$1" == "0" ]]; then
            say "Updating GRIP in $dir"
            git -C "$dir" pull --ff-only -q </dev/null || say "Could not update $dir, so this run uses it as it is."
        fi
    elif [[ -e "$dir" ]]; then
        problem "$dir exists and is not a Git clone of GRIP. Move it, or set GRIP_SUITE_DIR to another folder."
        return 1
    elif [[ "$1" == "1" ]]; then
        say "Dry run: a real run would clone GRIP from $url into $dir."
        return 2
    else
        say "Cloning GRIP into $dir"
        { mkdir -p "$(dirname "$dir")" && git clone -q "$url" "$dir" </dev/null; } || return 1
    fi
    if [[ ! -f "$dir/lib/suite_install/__main__.py" ]]; then
        problem "$dir has no suite installer (lib/suite_install). Update it, or set GRIP_SUITE_DIR to a newer clone."
        return 1
    fi
}

# remove_shim: remove the python3 link python_first made, and then its folder (GRIP#9505).
remove_shim() {
    if [[ -n "${SUITE_SHIM:-}" ]]; then
        rm -f "$SUITE_SHIM/python3" && rmdir "$SUITE_SHIM"
    fi
    return 0
}

# python_first <interpreter>: make `python3` mean that interpreter for this run.
# HAL's and HAPPI's commands call plain `python3`, and their proofs run them.
python_first() {
    local current shim
    current="$(command -v python3 2>/dev/null)"
    if [[ -n "$current" ]] && python_ok "$current"; then
        return 0
    fi
    # A named template: macOS's `mktemp -d` ignores TMPDIR, and a leftover is then recognisable.
    shim="$(mktemp -d "${TMPDIR:-/tmp}/grip-suite-python.XXXXXX")" && ln -s "$1" "$shim/python3" || return 1
    SUITE_SHIM="$shim"
    trap remove_shim EXIT
    PATH="$shim:$PATH"
    export PATH
    say "Note: python3 on your PATH is older than 3.11, so this run uses $1."
    say "HAL needs Python 3.11 or newer as python3: put $(dirname "$1") first on your PATH."
}

main() {
    local dry=0 python code
    case "${1:-}" in
        "") ;;
        --dry-run) dry=1 ;;
        -h | --help) print_help && return 0 ;;
        *) problem "unknown option: $1 (try --help)" && return 1 ;;
    esac
    if [[ "$(uname -s)" != "Darwin" && "$(uname -s)" != "Linux" ]]; then
        problem "this installer is for macOS and Linux."
        return 1
    fi
    python="$(prerequisites "$dry")"
    code=$?
    [[ "$code" == "0" ]] || return "$code"
    say "Using $("$python" --version 2>&1 </dev/null) at $python, and $(git --version </dev/null)."
    get_grip "$dry"
    code=$?
    [[ "$code" == "0" ]] || return "$code"
    python_first "$python" || return 1
    cd "$(suite_dir)" || return 1
    if [[ "$dry" == "1" ]]; then
        "$python" -m lib.suite_install --dry-run </dev/null
    else
        "$python" -m lib.suite_install </dev/null
    fi
}

if [[ "${GRIP_SUITE_RUN_MAIN:-1}" == "1" ]]; then
    main "$@"
fi
