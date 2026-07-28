#!/usr/bin/env bash
# One-file setup for macOS/Linux, matching setup.bat's Windows behavior:
# create the venv, install dependencies, ask for an LLM key if .env doesn't
# have one yet, run the test suite as a smoke check, and launch the web UI.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "============================================"
echo "  FoodPath -- NC FNS Eligibility Guide Setup"
echo "============================================"
echo

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 was not found on this machine."
    echo "Install Python 3.11 or newer from https://www.python.org/downloads/"
    exit 1
fi

if [ ! -f ".venv/bin/activate" ]; then
    echo "Creating a virtual environment in .venv ..."
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "Installing dependencies -- this can take a minute the first time..."
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

python scripts/setup_env.py

echo
echo "Running the test suite to confirm everything works..."
if ! python -m pytest -q; then
    echo
    echo "WARNING: some tests failed above. This can happen depending on"
    echo "which LLM provider you configured -- the app may still run fine."
    read -r -p "Press Enter to continue anyway, or Ctrl+C to stop here. "
fi

echo
echo "Starting FoodPath at http://127.0.0.1:8000 ..."
echo "Press Ctrl+C to stop the server when you're done."
echo
if command -v open >/dev/null 2>&1; then
    (sleep 1 && open "http://127.0.0.1:8000") &
elif command -v xdg-open >/dev/null 2>&1; then
    (sleep 1 && xdg-open "http://127.0.0.1:8000") &
fi
python -m uvicorn src.web:app --host 127.0.0.1 --port 8000
