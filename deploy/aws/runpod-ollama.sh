#!/usr/bin/env bash
set -eu
umask 077
exec 9>/workspace/lens/ollama.lock
flock -n 9 || exit 0
export OLLAMA_HOST=127.0.0.1:11434
export OLLAMA_MODELS=/workspace/lens/models
export OLLAMA_CONTEXT_LENGTH=8192
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_KEEP_ALIVE=30m
export OLLAMA_FLASH_ATTENTION=1
while true; do
    /workspace/lens/bin/ollama serve &
    server_pid=$!
    trap 'kill "$server_pid" 2>/dev/null || true; exit 0' TERM INT
    wait "$server_pid" || true
    sleep 5
done
