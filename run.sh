#!/usr/bin/env bash
# MedLens one-command launcher.
#   ./run.sh         -> http://localhost:8000 (this machine only)
#   ./run.sh --lan   -> also reachable from your phone on the same Wi-Fi
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
PORT="${PORT:-8000}"
HOST=127.0.0.1
if [ "$1" = "--lan" ]; then
  HOST=0.0.0.0
  IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  [ -z "$IP" ] && IP="$(ipconfig getifaddr en0 2>/dev/null || true)"
  echo "MedLens on your network: http://${IP:-<your-laptop-ip>}:$PORT"
  echo "(Phones get the 'take a photo' camera button; live preview needs https or localhost.)"
fi
exec .venv/bin/uvicorn app.main:app --reload --host "$HOST" --port "$PORT"
