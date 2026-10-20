#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
if ! docker version >/dev/null 2>&1; then
  echo "Open Docker Desktop and wait until it is running, then try again."
  exit 1
fi
docker compose -f compose.paper.yml up -d --build
docker compose -f compose.paper.yml exec paper python workbench.py menu
echo "Closing this menu does not stop the background paper worker. Pause it in the menu if needed."
