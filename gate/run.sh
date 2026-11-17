#!/bin/sh
# Runs gate.py through uv. Without uv, the hooks do nothing but say so at session start and Stop.
if command -v uv >/dev/null 2>&1; then
  exec uv run -q --script "$(dirname "$0")/gate.py" "$@"
fi
case "$1" in
session | stop)
  echo '{"systemMessage": "papers-please needs uv (https://docs.astral.sh/uv/), so its checks are off. Run /papers-please:doctor to set it up."}'
  ;;
esac
