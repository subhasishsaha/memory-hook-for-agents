#!/usr/bin/env python3
"""Standalone installer script for memory-hook.

Can be run directly via `python install.py` or downloaded and piped into python.
"""

import os
import sys
from pathlib import Path

# Try importing from local package if present
try:
    from memory_hook.installer import install_hooks
except ImportError:
    # If run in standalone mode without memory_hook on python path
    current_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(current_dir))
    from memory_hook.installer import install_hooks


def main():
    target_dir = Path.cwd()
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        target_dir = Path(sys.argv[1]).resolve()

    print(f"Installing universal memory-hook into: {target_dir}")
    installed = install_hooks(target_dir, providers=["all"])

    print("\n✓ Installation successful!")
    print("Configured providers:")
    for prov, files in installed.items():
        print(f"  • {prov}: {', '.join(files)}")

    print("\nDeterministic logging is active in .memory/short.log")
    print("Agent synthesis knowledge base is in .memory/long.log")


if __name__ == "__main__":
    main()
