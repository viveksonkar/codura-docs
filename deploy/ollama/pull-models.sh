#!/bin/sh
# Pull the models listed in OLLAMA_PULL_MODELS (comma-separated). Idempotent: already-present
# models are skipped. Exits non-zero if any pull fails so dependent services do not start.
set -eu

models="${OLLAMA_PULL_MODELS:-bge-m3,qwen3:4b}"
present="$(ollama list | awk 'NR>1 {print $1}')"

old_ifs="$IFS"
IFS=','
for model in $models; do
  IFS="$old_ifs"
  model="$(echo "$model" | tr -d ' ')"
  [ -z "$model" ] && continue
  if echo "$present" | grep -qx -e "$model" -e "$model:latest"; then
    echo "ollama-init: $model already present"
  else
    echo "ollama-init: pulling $model"
    ollama pull "$model"
  fi
  IFS=','
done
IFS="$old_ifs"
echo "ollama-init: done"
