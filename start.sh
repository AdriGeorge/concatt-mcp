#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$PROJECT_DIR/.venv/bin/python" -B "$PROJECT_DIR/concat_mcp.py"
