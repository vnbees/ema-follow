#!/bin/sh
set -e
cd "$(dirname "$0")"
echo "bot-ema-follow-trend: starting (PORT=${PORT:-unset} DATABASE_PATH=${DATABASE_PATH:-unset})" >&2
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python -m src.main
fi
exec python3 -m src.main
