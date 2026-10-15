#!/usr/bin/env bash
# Run one command in the sandbox container. Only the current directory is mounted (/work);
# files are written as the calling user. Network stays on for npm/pip installs.
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: sandbox.sh '<command>'" >&2; exit 2; }
exec docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e npm_config_cache=/tmp/.npm \
  --memory 3g --cpus 2 -v "$PWD":/work -w /work "${SANDBOX_IMAGE:?set by the harness (app/m2/agent.py)}" \
  timeout "${SANDBOX_TIMEOUT:-900}" bash -c "$*"
