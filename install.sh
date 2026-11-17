#!/usr/bin/env bash
# Universal one-line installer for Linux and macOS
set -e

TARGET_DIR="${1:-$(pwd)}"
echo "Installing memory-hook into $TARGET_DIR..."

if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
else
    echo "Error: Python 3 is required to install memory-hook."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$PYTHON_CMD" "$SCRIPT_DIR/install.py" "$TARGET_DIR"
