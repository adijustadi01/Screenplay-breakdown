#!/usr/bin/env bash
# Starts the app every time you open the codespace. The browser tab opens by itself.
cd "$(dirname "$0")/../backend"
export DATABASE_URL="sqlite:///$HOME/screenplay-breakdown/breakdown.db"
export UPLOAD_DIR="$HOME/screenplay-breakdown/uploads"
export SB_CONFIG_DIR="$HOME/screenplay-breakdown"
mkdir -p "$HOME/screenplay-breakdown"
echo "Screenplay Breakdown is starting — a browser tab will open (or use the PORTS tab, port 8000)."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
