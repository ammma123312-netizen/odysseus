#!/bin/sh
# Local LLM bootstrap for Odysseus on HF Spaces (free tier, CPU-only).
#
# Downloads a small open-source GGUF model on first run, then launches
# llama-cpp-python's OpenAI-compatible HTTP server on $ODYSSEUS_LOCAL_LLM_PORT
# (default 8080). Odysseus then talks to it as a normal OpenAI endpoint.
#
# Environment:
#   ODYSSEUS_LOCAL_LLM        1 to enable (default off — outside HF Spaces)
#   LOCAL_LLM_REPO            HF repo containing the GGUF (default Qwen2.5-3B)
#   LOCAL_LLM_FILE            filename inside the repo (default q4_k_m)
#   ODYSSEUS_LOCAL_LLM_PORT   port to bind on localhost (default 8080)
#   ODYSSEUS_LOCAL_LLM_CTX    context window in tokens (default 4096)
#   ODYSSEUS_LOCAL_LLM_THREADS CPU threads (default: all)
#   MODELS_DIR                cache dir for downloaded GGUFs (default /app/models)
#
# Why a script (not just CMD): we need this to run in the background
# next to uvicorn, AND we want the first-time download to happen as the
# non-root app user so the cached file stays writable across restarts.

set -e

if [ "${ODYSSEUS_LOCAL_LLM:-0}" != "1" ]; then
    # Disabled: noop. Odysseus will fall back to whatever external host
    # is configured via LLM_HOST / LLM_HOSTS (or no LLM at all).
    exit 0
fi

REPO="${LOCAL_LLM_REPO:-Qwen/Qwen2.5-3B-Instruct-GGUF}"
FILE="${LOCAL_LLM_FILE:-qwen2.5-3b-instruct-q4_k_m.gguf}"
PORT="${ODYSSEUS_LOCAL_LLM_PORT:-8080}"
CTX="${ODYSSEUS_LOCAL_LLM_CTX:-4096}"
MODELS_DIR="${MODELS_DIR:-/app/models}"

# CPU thread count: default = all available, capped at 4 to avoid starving
# Odysseus' own uvicorn workers on the 2-vCPU cpu-basic tier.
if [ -z "${ODYSSEUS_LOCAL_LLM_THREADS}" ]; then
    DETECTED=$(nproc 2>/dev/null || echo 2)
    if [ "$DETECTED" -gt 4 ]; then DETECTED=4; fi
    THREADS="$DETECTED"
else
    THREADS="$ODYSSEUS_LOCAL_LLM_THREADS"
fi

mkdir -p "$MODELS_DIR"
MODEL_PATH="$MODELS_DIR/$FILE"

if [ ! -f "$MODEL_PATH" ]; then
    echo "[local-llm] downloading $REPO / $FILE -> $MODEL_PATH"
    # huggingface_hub is already a hard dep (used by HF persistence + Cookbook).
    # Use it directly so the download stays consistent with the rest of the
    # codebase (same auth, same cache semantics, same progress format).
    python -c "
from huggingface_hub import hf_hub_download
import os, shutil
src = hf_hub_download(repo_id='$REPO', filename='$FILE', cache_dir='$MODELS_DIR/.cache')
# Copy out of the HF cache into a stable path llama.cpp can mmap.
shutil.copyfile(src, '$MODEL_PATH')
print('[local-llm] saved', '$MODEL_PATH', os.path.getsize('$MODEL_PATH'), 'bytes')
"
fi

echo "[local-llm] launching llama-cpp-python OpenAI server on :$PORT"
echo "[local-llm]   model=$MODEL_PATH  ctx=$CTX  threads=$THREADS"

# --n_ctx: context window. 4096 is a safe default for a 3B Q4 model on
#   ~4GB RAM. Bump via ODYSSEUS_LOCAL_LLM_CTX if you have headroom.
# --n_threads: CPU threads for generation. Capped above so uvicorn keeps
#   responsiveness on cpu-basic.
# --host 127.0.0.1: do NOT expose externally. Only Odysseus (same
#   container) talks to it. Public traffic still goes through Odysseus
#   on $APP_PORT.
exec python -m llama_cpp.server \
    --model "$MODEL_PATH" \
    --host 127.0.0.1 \
    --port "$PORT" \
    --n_ctx "$CTX" \
    --n_threads "$THREADS" \
    --chat_format "chatml" \
    --verbose false
