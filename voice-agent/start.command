#!/bin/sh
# Starts the voice agent on Mac or Linux:  sh start.command
# (On a Mac you can also double-click it; the first time, right-click it and choose Open.)
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "The agent isn't installed yet. Run: sh install.sh"
  exit 1
fi
exec .venv/bin/python start.py
