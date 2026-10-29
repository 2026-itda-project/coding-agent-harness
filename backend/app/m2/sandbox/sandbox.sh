#!/usr/bin/env bash
# Run one command in the sandbox container. Only the current directory is mounted (/work);
# files are written as the calling user. Network stays on for npm/pip installs.
# The limits are agent.LIMITS, written out so a caller cannot loosen them through the
# environment. The label lets the harness remove containers that outlive their caller.
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: sandbox.sh '<command>'" >&2; exit 2; }
exec docker run --rm --label "harness.run=${HARNESS_RUN_ID:?set by the harness (app/m2/agent.py)}" \
  --user "$(id -u):$(id -g)" -e HOME=/tmp -e npm_config_cache=/tmp/.npm \
  --memory 3g --cpus 2 --pids-limit 512 --cap-drop ALL --security-opt no-new-privileges \
  -v "$PWD":/work -w /work "${SANDBOX_IMAGE:?set by the harness (app/m2/agent.py)}" \
  timeout -k 10 "${SANDBOX_TIMEOUT:-900}" bash -c "$*"
