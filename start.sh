#!/usr/bin/env bash
# Draftease — run locally with one command.
#   bash start.sh
# Then open http://127.0.0.1:8000
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install it from https://www.python.org/downloads/ or run: brew install python"
  exit 1
fi

if [ ! -d venv ]; then
  echo "• Creating virtual environment..."
  python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate

echo "• Installing dependencies (first run only)..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# Stable secret so logins survive restarts.
if [ -z "$DRAFTEASE_SECRET_KEY" ] && [ ! -f .draftease_secret ]; then
  python3 -c "import secrets; open('.draftease_secret','w').write(secrets.token_urlsafe(48))"
fi

echo ""
echo "============================================================"
echo "  Draftease is running:  http://127.0.0.1:8000"
echo "  Open that in your browser. Press Ctrl-C to stop."
echo "============================================================"
echo ""
exec uvicorn app:app --host 127.0.0.1 --port 8000
