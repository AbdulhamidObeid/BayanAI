#!/bin/bash
# ============================================================
# Bayan-AI Web Dashboard Launcher
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# Verify .env exists
if [ ! -f ".env" ]; then
    echo "[bayan] WARNING: .env file not found. GEMINI_API_KEY may be missing."
    echo "[bayan] Semantic questions will abstain unless a previously validated decision is cached."
fi

# Verify dependencies
python3 -c "import fastapi, uvicorn, pydantic" 2>/dev/null || {
    echo "[bayan] Install dependencies with: python3 -m pip install -r requirements.txt"
    exit 1
}

echo "[bayan] Starting Bayan-AI web server..."
echo "[bayan] Dashboard: http://${BAYAN_HOST:-127.0.0.1}:${BAYAN_PORT:-8000}"
echo "[bayan] API Docs:   http://${BAYAN_HOST:-127.0.0.1}:${BAYAN_PORT:-8000}/docs"
echo "[bayan] Open the Dashboard URL above; do not open the HTML template as a file."
echo "[bayan] Press Ctrl+C to stop."
echo ""

python3 -m uvicorn src.web.app:app --host "${BAYAN_HOST:-127.0.0.1}" --port "${BAYAN_PORT:-8000}"
