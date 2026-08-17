#!/bin/zsh
cd "$(dirname "$0")"
clear
echo "Starting local bot — logs below (Ctrl+C to stop)"
echo "Dashboard: http://localhost:8080"
echo "----------------------------------------"
exec .venv/bin/python -m src.main
