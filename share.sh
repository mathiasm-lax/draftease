#!/usr/bin/env bash
# Draftease — get a PUBLIC web address with one command.
#   bash share.sh
# Starts the app and opens a Cloudflare tunnel; copy the printed
# https://....trycloudflare.com link and open it in any browser.
#
# Note: the link stays live only while this command is running and your
# Mac is awake. For an always-on address, see GET_ONLINE.md (Render).
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install from https://www.python.org/downloads/ or: brew install python"
  exit 1
fi

# --- app deps ---
if [ ! -d venv ]; then python3 -m venv venv; fi
# shellcheck disable=SC1091
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
if [ -z "$DRAFTEASE_SECRET_KEY" ] && [ ! -f .draftease_secret ]; then
  python3 -c "import secrets; open('.draftease_secret','w').write(secrets.token_urlsafe(48))"
fi

# --- cloudflared (the tunnel tool) ---
if ! command -v cloudflared >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "• Installing cloudflared via Homebrew..."
    brew install cloudflared
  else
    echo "cloudflared is needed for the public link. Install Homebrew (https://brew.sh) then run:"
    echo "    brew install cloudflared"
    echo "…or download it from:"
    echo "    https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
    exit 1
  fi
fi

# --- start app in background, tunnel in foreground ---
uvicorn app:app --host 127.0.0.1 --port 8000 >/tmp/draftease.log 2>&1 &
SVPID=$!
trap 'kill $SVPID 2>/dev/null' EXIT
sleep 3

echo ""
echo "============================================================"
echo "  Draftease is going public. Look for the line below that"
echo "  looks like:   https://something.trycloudflare.com"
echo "  That is your web address — open it in any browser."
echo "  Press Ctrl-C here to stop sharing."
echo "============================================================"
echo ""
cloudflared tunnel --url http://127.0.0.1:8000
