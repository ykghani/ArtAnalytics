#!/bin/bash
# Fixed, narrow control surface for loop.py, invoked via sudo by the
# openclaw user. Only the subcommands below are reachable — no free-form
# shell passthrough.
set -euo pipefail

PROJECT_ROOT=/home/pi/ArtServe/ArtAnalytics
UV=/home/pi/.local/bin/uv
cd "$PROJECT_ROOT"

case "${1:-}" in
  status)
    exec "$UV" run python scripts/loop.py status
    ;;
  add)
    [ -n "${2:-}" ] || { echo "usage: loop_ctl.sh add <slug>" >&2; exit 1; }
    exec "$UV" run python scripts/loop.py add "$2"
    ;;
  resume)
    [ -n "${2:-}" ] || { echo "usage: loop_ctl.sh resume <slug> [--from PHASE]" >&2; exit 1; }
    slug=$2
    shift 2
    exec "$UV" run python scripts/loop.py resume "$slug" "$@"
    ;;
  skip)
    [ -n "${2:-}" ] || { echo "usage: loop_ctl.sh skip <slug>" >&2; exit 1; }
    exec "$UV" run python scripts/loop.py skip "$2"
    ;;
  needs-human)
    if [ -f NEEDS_HUMAN.md ]; then cat NEEDS_HUMAN.md; else echo "none"; fi
    ;;
  *)
    echo "usage: loop_ctl.sh {status|add <slug>|resume <slug> [--from PHASE]|skip <slug>|needs-human}" >&2
    exit 1
    ;;
esac
