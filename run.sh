#!/usr/bin/env bash
# Mac/Linux: creates a virtualenv, installs dependencies and starts the gateway.
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
if [ -f .env ]; then set -a; . ./.env; set +a; fi
echo ""
echo "  Dashboard:  http://localhost:8000/dashboard"
echo "  API docs:   http://localhost:8000/docs"
echo ""
exec uvicorn app.main:app --host 127.0.0.1 --port 8000
