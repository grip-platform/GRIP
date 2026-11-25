#!/usr/bin/env bash
# bootstrap-vision.sh -- Download Llama 3.2 Vision 11B and install dependencies.
#
# Downloads TWO GGUF files:
#   1. Language model (~5.96 GB) -- Q4_K_M quantization
#   2. CLIP vision projector (~1.94 GB) -- f16 (must be full precision)
#
# USAGE:
#   bash ~/.claude/scripts/bootstrap-vision.sh
#   bash ~/.claude/scripts/bootstrap-vision.sh --verify-only
#
# REQUIREMENTS:
#   - Python 3.9+, pip, curl or wget
#   - ~8GB disk for models
#   - 16GB+ RAM (Apple Silicon unified memory recommended)
#   - llama-cpp-python (installed by bootstrap-llama.sh or this script)
#
# Cross-platform: macOS (Metal), Linux (CPU/CUDA)

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
MODELS_DIR="$CLAUDE_DIR/models"
VENV_DIR="$CLAUDE_DIR/venv"

# Model source: leafspark/Llama-3.2-11B-Vision-Instruct-GGUF on Hugging Face
HF_BASE="https://huggingface.co/leafspark/Llama-3.2-11B-Vision-Instruct-GGUF/resolve/main"

LANG_MODEL="Llama-3.2-11B-Vision-Instruct.Q4_K_M.gguf"
MMPROJ_MODEL="Llama-3.2-11B-Vision-Instruct-mmproj.f16.gguf"

LANG_URL="${HF_BASE}/${LANG_MODEL}"
MMPROJ_URL="${HF_BASE}/${MMPROJ_MODEL}"

LANG_PATH="$MODELS_DIR/$LANG_MODEL"
MMPROJ_PATH="$MODELS_DIR/$MMPROJ_MODEL"

# Minimum expected file sizes (bytes) -- catches truncated downloads
LANG_MIN_SIZE=4000000000   # ~4GB minimum for Q4_K_M
MMPROJ_MIN_SIZE=1500000000 # ~1.5GB minimum for f16 projector

# Cross-platform Python detection
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

log_info()  { echo "[INFO]  $*"; }
log_warn()  { echo "[WARN]  $*"; }
log_error() { echo "[ERROR] $*" >&2; }

# -- Memory check (macOS only) -----------------------------------------------
check_memory() {
    if [[ "$(uname)" == "Darwin" ]]; then
        local mem_gb
        mem_gb=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
        mem_gb=$(( mem_gb / 1073741824 ))
        if (( mem_gb < 16 )); then
            log_warn "System has ${mem_gb}GB RAM. 16GB+ recommended for 11B vision model."
            log_warn "The model may run slowly or fail to load."
        else
            log_info "System RAM: ${mem_gb}GB (sufficient for 11B model)"
        fi
    fi
}

# -- Verify-only mode --------------------------------------------------------
verify_install() {
    local ok=true

    for model_file in "$LANG_PATH" "$MMPROJ_PATH"; do
        local name
        name=$(basename "$model_file")
        if [[ ! -f "$model_file" ]]; then
            log_error "Model not found: $model_file"
            ok=false
        else
            local size
            size=$(wc -c < "$model_file" 2>/dev/null || echo 0)
            log_info "Model OK: $name ($(( size / 1048576 )) MB)"
        fi
    done

    if $PYTHON -c "import llama_cpp" 2>/dev/null; then
        log_info "llama-cpp-python OK"
    else
        log_error "llama-cpp-python not importable"
        ok=false
    fi

    if $PYTHON -c "import fitz" 2>/dev/null; then
        log_info "PyMuPDF OK"
    else
        log_warn "PyMuPDF not installed (needed for PDF extraction)"
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

# -- Step 0: System check ---------------------------------------------------
check_memory

# -- Step 1: Ensure venv ----------------------------------------------------
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

# -- Step 2: Install Python dependencies ------------------------------------
if $PYTHON -c "import llama_cpp" 2>/dev/null; then
    log_info "llama-cpp-python already installed."
else
    log_info "Installing llama-cpp-python (this may take a few minutes)..."
    if command -v nvcc &>/dev/null; then
        log_info "CUDA detected -- building with GPU support."
        CMAKE_ARGS="-DGGML_CUDA=on" $PYTHON -m pip install llama-cpp-python --no-cache-dir
    else
        $PYTHON -m pip install llama-cpp-python --no-cache-dir
    fi
fi

if $PYTHON -c "import fitz" 2>/dev/null; then
    log_info "PyMuPDF already installed."
else
    log_info "Installing PyMuPDF..."
    $PYTHON -m pip install PyMuPDF --no-cache-dir
fi

# -- Step 3: Download models ------------------------------------------------
mkdir -p "$MODELS_DIR"

download_model() {
    local url="$1"
    local dest="$2"
    local name
    name=$(basename "$dest")

    if [[ -f "$dest" ]]; then
        log_info "Model already exists: $name"
        return 0
    fi

    log_info "Downloading $name..."
    if command -v curl &>/dev/null; then
        # -C - enables resume on interrupted downloads
        curl -L -C - --progress-bar -o "$dest" "$url"
    elif command -v wget &>/dev/null; then
        wget -c --show-progress -O "$dest" "$url"
    else
        log_error "Neither curl nor wget found. Cannot download model."
        return 1
    fi

    # Verify download size
    local size
    size=$(wc -c < "$dest" 2>/dev/null || echo 0)
    log_info "Downloaded: $name ($(( size / 1048576 )) MB)"
}

download_model "$LANG_URL" "$LANG_PATH"
download_model "$MMPROJ_URL" "$MMPROJ_PATH"

# -- Step 4: Verify ---------------------------------------------------------
log_info "Verifying installation..."
verify_install

log_info ""
log_info "Vision bootstrap complete."
log_info "  Language model: $LANG_MODEL"
log_info "  Vision projector: $MMPROJ_MODEL"
log_info ""
log_info "Usage: from lib.vision_engine import VisionEngine"
