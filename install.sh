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

INSTALL_PY_URL="https://raw.githubusercontent.com/subhasishsaha/memory-hook-for-agents/main/install.py"
curl -fsSL "$INSTALL_PY_URL" | "$PYTHON_CMD" - "$TARGET_DIR"
