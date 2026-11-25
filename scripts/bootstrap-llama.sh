#!/usr/bin/env bash
# bootstrap-llama.sh -- Download Llama 3.2 3B and install llama-cpp-python.
#
# USAGE:
#   bash ~/.claude/scripts/bootstrap-llama.sh
#   bash ~/.claude/scripts/bootstrap-llama.sh --verify-only
#
# REQUIREMENTS:
#   - Python 3.9+, pip, curl or wget
#   - ~2.5GB disk for model + package
#   - C compiler (llama-cpp-python builds from source)
#
# Cross-platform: macOS (Metal), Linux (CPU/CUDA), Windows Git Bash

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
MODELS_DIR="$CLAUDE_DIR/models"
VENV_DIR="$CLAUDE_DIR/venv"

MODEL_NAME="Llama-3.2-3B-Instruct-Q4_K_M.gguf"
MODEL_URL="https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/main/${MODEL_NAME}"
MODEL_PATH="$MODELS_DIR/$MODEL_NAME"

# Cross-platform Python detection (Windows: python, macOS/Linux: python3)
PYTHON="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "")"
if [[ -z "$PYTHON" ]]; then
    echo "ERROR: Python 3.9+ required but not found."
    exit 1
fi

# Prefer venv Python if available
if [[ -f "$VENV_DIR/bin/python3" ]]; then
    PYTHON="$VENV_DIR/bin/python3"
elif [[ -f "$VENV_DIR/Scripts/python.exe" ]]; then
    PYTHON="$VENV_DIR/Scripts/python.exe"
fi

PIP="$PYTHON -m pip"

log_info()  { echo "[INFO]  $*"; }
log_warn()  { echo "[WARN]  $*"; }
log_error() { echo "[ERROR] $*" >&2; }

# -- Verify-only mode ---------------------------------------------------
verify_install() {
    local ok=true

    if [[ ! -f "$MODEL_PATH" ]]; then
        log_error "Model not found: $MODEL_PATH"
        ok=false
    else
        local size
        size=$(wc -c < "$MODEL_PATH" 2>/dev/null || echo 0)
        if (( size < 1000000000 )); then
            log_warn "Model file seems too small (${size} bytes). May be corrupt."
            ok=false
        else
            log_info "Model OK: $MODEL_NAME ($(( size / 1048576 )) MB)"
        fi
    fi

    if $PYTHON -c "import llama_cpp" 2>/dev/null; then
        log_info "llama-cpp-python OK"
    else
        log_error "llama-cpp-python not importable"
        ok=false
    fi

    if $ok; then
        log_info "All checks passed."
        return 0
    else
        log_error "Verification failed."
        return 1
    fi
}

if [[ "${1:-}" == "--verify-only" ]]; then
    verify_install
    exit $?
fi

# -- Step 1: Ensure venv ------------------------------------------------
if [[ ! -d "$VENV_DIR" ]]; then
    log_info "Creating virtual environment at $VENV_DIR..."
    $PYTHON -m venv "$VENV_DIR"
fi

# Activate venv for pip
if [[ -f "$VENV_DIR/bin/activate" ]]; then
    source "$VENV_DIR/bin/activate"
elif [[ -f "$VENV_DIR/Scripts/activate" ]]; then
    source "$VENV_DIR/Scripts/activate"
fi

# -- Step 2: Install llama-cpp-python -----------------------------------
if $PYTHON -c "import llama_cpp" 2>/dev/null; then
    log_info "llama-cpp-python already installed."
else
    log_info "Installing llama-cpp-python (this may take a few minutes)..."
    # On macOS, Metal support is automatic. On Linux, detect CUDA.
    if command -v nvcc &>/dev/null; then
        log_info "CUDA detected -- building with GPU support."
        CMAKE_ARGS="-DGGML_CUDA=on" $PIP install llama-cpp-python --no-cache-dir
    else
        $PIP install llama-cpp-python --no-cache-dir
    fi
fi

# -- Step 3: Download model ---------------------------------------------
mkdir -p "$MODELS_DIR"

if [[ -f "$MODEL_PATH" ]]; then
    log_info "Model already exists: $MODEL_PATH"
else
    log_info "Downloading $MODEL_NAME (~2GB)..."
    if command -v curl &>/dev/null; then
        curl -L --progress-bar -o "$MODEL_PATH" "$MODEL_URL"
    elif command -v wget &>/dev/null; then
        wget --show-progress -O "$MODEL_PATH" "$MODEL_URL"
    else
        log_error "Neither curl nor wget found. Cannot download model."
        exit 1
    fi
    log_info "Download complete."
fi

# -- Step 4: Verify -----------------------------------------------------
log_info "Verifying installation..."
verify_install

log_info "Llama bootstrap complete. Model: $MODEL_NAME"
log_info "Usage: from lib.llama_engine import LlamaEngine"
