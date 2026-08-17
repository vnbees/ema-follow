#!/bin/sh
set -e
echo "bot-ema-follow-trend: starting (PORT=${PORT:-unset} DATABASE_PATH=${DATABASE_PATH:-unset})" >&2
exec python3 -m src.main
