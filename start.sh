#!/bin/bash
echo ""
echo " =========================================="
echo "   OpenMTOps — Starting up"
echo " =========================================="
echo ""

# ── Free port 5000 if a stale Python process is holding it ──────────────────
PID=$(lsof -ti tcp:5000 2>/dev/null)
if [ -n "$PID" ]; then
    PNAME=$(ps -p "$PID" -o comm= 2>/dev/null)
    if [[ "$PNAME" == python* ]]; then
        echo "  [!] Stale Python process on port 5000 (PID $PID) — killing it..."
        kill -9 "$PID"
        sleep 0.8
        echo "  [+] Port 5000 is now free."
    else
        echo "  [!] Port 5000 held by non-Python process (PID $PID) — skipping."
    fi
else
    echo "  [+] Port 5000 is free."
fi

echo ""

# ── Virtualenv: validate, recreate if broken, create if missing ──────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -d "venv" ] && [ ! -f "venv/pyvenv.cfg" ]; then
    echo "  [!] venv is broken — pyvenv.cfg missing. Recreating..."
    rm -rf venv
    python3 -m venv venv || { echo "  [!] Failed to create venv. Is Python 3 installed?"; read -rp "Press Enter to close..."; exit 1; }
    echo "  [+] venv recreated successfully."
    echo "  [!] Installing dependencies..."
    source venv/bin/activate
    pip install git+https://github.com/dhan-oss/DhanHQ-py.git --quiet
    pip install -r requirements.txt --quiet
    echo "  [+] Dependencies installed."
elif [ ! -d "venv" ]; then
    echo "  [!] No venv found — creating one..."
    python3 -m venv venv || { echo "  [!] Failed to create venv. Is Python 3 installed?"; read -rp "Press Enter to close..."; exit 1; }
    echo "  [+] venv created."
    echo "  [!] Installing dependencies..."
    source venv/bin/activate
    pip install git+https://github.com/dhan-oss/DhanHQ-py.git --quiet
    pip install -r requirements.txt --quiet
    echo "  [+] Dependencies installed."
else
    echo "  [+] Activating virtualenv..."
    source venv/bin/activate
fi

# ── Start the app ─────────────────────────────────────────────────────────────
echo "  [+] Launching app..."
echo ""
python app.py

# ── Keep terminal open on crash ───────────────────────────────────────────────
echo ""
echo "  [!] App exited. Press Enter to close."
read -rp ""
