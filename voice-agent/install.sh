#!/bin/sh
# Installs the voice agent on Mac or Linux:  sh install.sh
set -e
cd "$(dirname "$0")"
echo "Installing the voice agent..."
PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/ and run this again."
  exit 1
fi
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt
[ -f .env ] || cp .env.example .env
echo "Done. Next, add your settings to .env (README Part 1, step 5)."
