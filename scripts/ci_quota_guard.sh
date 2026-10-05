#!/usr/bin/env bash
# Runs a command. If it fails because the embedding provider answered 429,
# say so, so nobody mistakes a quota wall for a retrieval-quality regression.
set -uo pipefail
log="$(mktemp)"
trap 'rm -f "$log"' EXIT
"$@" 2>&1 | tee "$log"
status=${PIPESTATUS[0]}
if [ "$status" -ne 0 ] && grep -q "429 Too Many Requests" "$log"; then
  echo "::error title=Embedding quota exhausted::The embedding provider returned 429. This is a provider quota problem, not a retrieval-quality regression. Re-run later or check the LiteLLM key budget."
fi
exit "$status"
