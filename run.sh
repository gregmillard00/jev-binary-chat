#!/usr/bin/env bash
# Start/stop by the PORT it holds, never by a command-line pattern.
# `pkill -f "python3 app.py"` also matches the shell running this script and kills
# that instead - it has cost a shell seven times on this box.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-8975}"
PY=/home/greg/personal-agent/venv/bin/python3
listening(){ ss -ltn 2>/dev/null | grep -q "127.0.0.1:${PORT} "; }
holder(){ ss -ltnp 2>/dev/null | grep "127.0.0.1:${PORT} " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2; }
case "${1:-start}" in
  start)
    listening && { echo "already listening on ${PORT} (pid $(holder))"; exit 0; }
    cd "$HERE"
    setsid nohup "$PY" app.py >> "$HERE/run.log" 2>&1 < /dev/null &
    for _ in $(seq 1 40); do listening && break; sleep 0.25; done
    listening && echo "started on 127.0.0.1:${PORT} (pid $(holder))" || { echo "FAILED, see run.log"; tail -5 "$HERE/run.log"; exit 1; }
    ;;
  stop)
    p="$(holder || true)"
    [ -n "${p:-}" ] && { kill "$p"; echo "stopped pid $p"; } || echo "not running on ${PORT}"
    ;;
  restart) "$0" stop || true; sleep 1; "$0" start ;;
  status) listening && echo "listening on ${PORT} (pid $(holder))" || echo "not running" ;;
  *) echo "usage: $0 {start|stop|restart|status}"; exit 2 ;;
esac
