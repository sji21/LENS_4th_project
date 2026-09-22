#!/usr/bin/env bash
set -eu
umask 077
mkdir -p /workspace/lens/logs
nohup /workspace/lens/runpod-ollama.sh >>/workspace/lens/logs/ollama.log 2>&1 </dev/null &
