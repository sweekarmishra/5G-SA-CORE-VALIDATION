#!/usr/bin/env bash
# Mac / Linux: bash live_demo.sh   (opens the live console in your browser; Ctrl+C to stop)
cd "$(dirname "$0")" || exit 1
PY=python3; command -v python3 >/dev/null 2>&1 || PY=python
exec $PY live_console.py
