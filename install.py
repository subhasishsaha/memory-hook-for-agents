#!/usr/bin/env python3
"""Standalone installer script for memory-hook.

Can be run directly via `python install.py` or downloaded and piped into python.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Try importing from local package or existing environment
install_hooks = None

try:
    from memory_hook.installer import install_hooks
    from memory_hook.standalone_template import STANDALONE_RUNNER_CODE
except ImportError:
    current_dir = Path(__file__).resolve().parent
    if (current_dir / "memory_hook").is_dir():
        sys.path.insert(0, str(current_dir))
        try:
            from memory_hook.installer import install_hooks
            from memory_hook.standalone_template import STANDALONE_RUNNER_CODE
        except ImportError:
            pass

if install_hooks is None:
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "memory-hook"],
            capture_output=True,
            check=False,
        )
        from memory_hook.installer import install_hooks
        from memory_hook.standalone_template import STANDALONE_RUNNER_CODE
    except Exception:
        pass


def _standalone_install(
    target_dir: Path,
    providers: Optional[List[str]] = None,
    dry_run: bool = False,
) -> dict:
    """Self-contained fallback installer when memory-hook package is not importable."""
    memory_dir = target_dir / ".memory"
    bin_dir = memory_dir / "bin"
    if dry_run:
        return {"dry_run": True}

    memory_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)

    # Migrate legacy .txt files
    short_txt = memory_dir / "short.txt"
    short_log = memory_dir / "short.log"
    if short_txt.is_file():
        if not short_log.is_file():
            try: short_txt.rename(short_log)
            except Exception: pass
        else:
            try: short_txt.unlink()
            except Exception: pass

    long_txt = memory_dir / "long.txt"
    long_log = memory_dir / "long.log"
    if long_txt.is_file():
        if not long_log.is_file():
            try: long_txt.rename(long_log)
            except Exception: pass
        else:
            try: long_txt.unlink()
            except Exception: pass

    if not short_log.exists():
        short_log.touch()

    if not long_log.exists():
        now_iso = datetime.now(timezone.utc).isoformat()
        long_log.write_text(
            f"# Project Memory & Knowledge Base\n"
            f"> Last Updated: {now_iso} | Provider: initial-setup | Synced Through Event: #0\n\n"
            f"## 1. Project Overview & Primary Goals\n"
            f"- **Mission**: Project root at {target_dir.name}\n\n"
            f"## 2. Technology Stack & Environment\n"
            f"- Auto-detected\n\n"
            f"## 3. Architecture & Core Decisions\n"
            f"- [x] Initialized cross-provider memory hook tracking.\n\n"
            f"## 4. Completed Work & Milestones\n"
            f"- [x] Repository configured with deterministic event logging.\n\n"
            f"## 5. Current State & Active Focus\n"
            f"- Initial project setup and memory initialization.\n\n"
            f"## 6. Next Immediate Steps\n"
            f"1. Execute project requirements.\n"
            f"2. Update `.memory/long.log` when prompted.\n",
            encoding="utf-8",
        )

    state_file = memory_dir / "state.json"
    state_data = {
        "version": 2,
        "current_seq": 0,
        "last_compacted_seq": 0,
        "total_events": 0,
        "last_updated_ts": None,
        "last_updated_provider": None,
        "total_lifetime_tokens": 0,
        "last_compacted_tokens": 0,
        "short_log_tokens": 0,
        "window_start_seq": 1,
        "pending_compaction": None,
        "runner_sha256": None,
    }
    if state_file.is_file():
        try:
            loaded = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state_data.update(loaded)
        except Exception:
            pass

    # Synchronized Standalone runner from canonical template
    runner_code = STANDALONE_RUNNER_CODE
    temp_source = bin_dir / "memory_hook_runner.py"
    compiled_runner = bin_dir / "memory_hook_runner.pyc"

    if compiled_runner.exists():
        try:
            if os.name == "nt":
                subprocess.run(["attrib", "-R", str(compiled_runner)], check=False, capture_output=True)
            else:
                compiled_runner.chmod(0o755)
        except Exception:
            pass

    temp_source.write_text(runner_code, encoding="utf-8")
    import py_compile
    py_compile.compile(str(temp_source), cfile=str(compiled_runner), doraise=True)
    try: temp_source.unlink()
    except Exception: pass

    sha256 = None
    try:
        with open(compiled_runner, "rb") as f:
            sha256 = hashlib.sha256(f.read()).hexdigest()
        state_data["runner_sha256"] = sha256
    except Exception:
        pass
    state_file.write_text(json.dumps(state_data, indent=2), encoding="utf-8")

    try:
        if os.name == "nt":
            subprocess.run(["attrib", "+R", str(compiled_runner)], check=False, capture_output=True)
        else:
            compiled_runner.chmod(0o555)
    except Exception:
        pass

    proto_instructions = (
        "# Universal Memory Hook Protocol (.memory/)\n\n"
        "This repository is integrated with the **Cross-Provider Memory Hook**.\n"
        "All agent activities (user requests, tool calls, tool responses, model outputs) are deterministically "
        "recorded in `.memory/short.log`, maintaining a rolling context window of **100,000 tokens** (100k tokens).\n\n"
        "## Core Directives for All Agents:\n\n"
        "1. **Context Restoration at Session Start**:\n"
        "   - At the beginning of every session or task, read `.memory/long.log` first.\n"
        "   - If needed, read the tail of `.memory/short.log` (last 20-30 lines) to catch up on the most recent actions taken by any previous agent.\n"
        "   - Never ask the user to re-explain what the project is about or what has already been built.\n\n"
        "2. **Long-Term Memory Compaction**:\n"
        "   - Whenever you receive a `[MEMORY HOOK UPDATE NOTICE]` indicating ~10,000 tokens have accumulated, review the uncompacted tail of `.memory/short.log` alongside the existing `.memory/long.log`.\n"
        "   - Use your file editing tools to update `.memory/long.log`:\n"
        "     - Update header metadata: `Last Updated`, `Provider`, and `Synced Through Event: #<seq>`.\n"
        "     - Update Completed Work & Milestones with newly finished items.\n"
        "     - Update Current State & Active Focus with what you are working on right now.\n"
        "     - Update Next Immediate Steps with pending tasks.\n"
        "     - Preserve and append any newly established Architecture & Core Decisions.\n"
        "     - Keep content concise, high-density, and structured.\n\n"
        "3. **Security & Immutability Directives**:\n"
        "   - The runner at `.memory/bin/memory_hook_runner.pyc` is locally compiled bytecode locked with OS read-only permissions.\n"
        "   - Under NO circumstances should any agent attempt to edit, rewrite, replace, or delete files inside `.memory/bin/` or `.memory/state.json`.\n"
        "   - All memory updates must strictly be applied to `.memory/long.log`.\n"
    )

    def append_or_create_instructions(file_path: Path, full_content: str, append_part: str):
        if not file_path.exists():
            file_path.write_text(full_content.strip() + "\n", encoding="utf-8")
        else:
            existing = file_path.read_text(encoding="utf-8")
            if "Universal Memory Hook Protocol" not in existing:
                sep = "\n\n---\n\n" if existing.strip() else ""
                file_path.write_text(existing + sep + append_part.strip() + "\n", encoding="utf-8")

    def backup_config(f_path: Path):
        if f_path.is_file():
            shutil.copy2(f_path, f_path.with_name(f"{f_path.name}.bak"))

    def read_json_safe(f_path: Path) -> dict:
        if not f_path.is_file(): return {}
        content = f_path.read_text(encoding="utf-8").strip()
        if not content: return {}
        try:
            val = json.loads(content)
            return val if isinstance(val, dict) else {}
        except Exception:
            backup_config(f_path)
            raise RuntimeError(f"Config {f_path} is malformed JSON. Backup created.")

    root_abs = target_dir.resolve().as_posix()
    runner_abs = (bin_dir / "memory_hook_runner.pyc").resolve().as_posix()
    installed_files = {}

    targets = providers or ["all"]
    if "all" in targets:
        targets = ["antigravity", "claude", "codex", "cursor"]

    for p in targets:
        p_clean = p.lower().strip()
        if p_clean in ["antigravity", "gemini"]:
            agents_dir = target_dir / ".agents"
            agents_dir.mkdir(parents=True, exist_ok=True)
            hooks_file = agents_dir / "hooks.json"
            backup_config(hooks_file)
            existing_hooks = read_json_safe(hooks_file)
            r_arg = f'"{runner_abs}"' if " " in runner_abs else runner_abs
            rt_arg = f'"{root_abs}"' if " " in root_abs else root_abs
            existing_hooks["memory-hook"] = {
                "PreInvocation": [{"type": "command", "command": f'python {r_arg} --provider antigravity --event PreInvocation --root {rt_arg}'}],
                "PreToolUse": [{"matcher": ".*", "hooks": [{"type": "command", "command": f'python {r_arg} --provider antigravity --event PreToolUse --root {rt_arg}'}]}],
                "PostToolUse": [{"matcher": ".*", "hooks": [{"type": "command", "command": f'python {r_arg} --provider antigravity --event PostToolUse --root {rt_arg}'}]}],
                "Stop": [{"type": "command", "command": f'python {r_arg} --provider antigravity --event Stop --root {rt_arg}'}],
            }
            hooks_file.write_text(json.dumps(existing_hooks, indent=2), encoding="utf-8")
            append_or_create_instructions(target_dir / "AGENTS.md", f"# Antigravity Agent Guidelines\n\n{proto_instructions}", proto_instructions)
            installed_files["antigravity"] = [".agents/hooks.json", "AGENTS.md"]

        elif p_clean in ["claude", "claude-code"]:
            claude_dir = target_dir / ".claude"
            claude_dir.mkdir(parents=True, exist_ok=True)
            claude_settings = claude_dir / "settings.json"
            backup_config(claude_settings)
            existing_claude_settings = read_json_safe(claude_settings)
            hooks = existing_claude_settings.get("hooks", {})
            if not isinstance(hooks, dict): hooks = {}

            def merge_claude(event_name, cmd, matcher=""):
                lst = hooks.get(event_name, [])
                if not isinstance(lst, list): lst = []
                entry = {"matcher": matcher, "hooks": [{"type": "command", "command": cmd}]}
                for i, existing in enumerate(lst):
                    if any("memory_hook_runner.pyc" in str(h.get("command", "")) for h in existing.get("hooks", [])):
                        lst[i] = entry
                        hooks[event_name] = lst
                        return
                lst.append(entry)
                hooks[event_name] = lst

            merge_claude("UserPromptSubmit", f'python "{runner_abs}" --provider claude-code --event UserPromptSubmit --root "{root_abs}"')
            merge_claude("PreToolUse", f'python "{runner_abs}" --provider claude-code --event PreToolUse --root "{root_abs}"', matcher=".*")
            merge_claude("PostToolUse", f'python "{runner_abs}" --provider claude-code --event PostToolUse --root "{root_abs}"', matcher=".*")
            merge_claude("Stop", f'python "{runner_abs}" --provider claude-code --event Stop --root "{root_abs}"')
            existing_claude_settings["hooks"] = hooks
            claude_settings.write_text(json.dumps(existing_claude_settings, indent=2), encoding="utf-8")
            append_or_create_instructions(target_dir / "CLAUDE.md", f"# Claude Code Guidelines\n\n{proto_instructions}", proto_instructions)
            installed_files["claude"] = [".claude/settings.json", "CLAUDE.md"]

        elif p_clean in ["codex", "openai"]:
            codex_dir = target_dir / ".codex"
            codex_dir.mkdir(parents=True, exist_ok=True)
            codex_hooks = codex_dir / "hooks.json"
            backup_config(codex_hooks)
            existing_codex_hooks = read_json_safe(codex_hooks)

            def merge_codex(event_name, cmd):
                lst = existing_codex_hooks.get(event_name, [])
                if not isinstance(lst, list): lst = []
                entry = {"type": "command", "command": cmd}
                for i, existing in enumerate(lst):
                    if "memory_hook_runner.pyc" in str(existing.get("command", "")):
                        lst[i] = entry
                        existing_codex_hooks[event_name] = lst
                        return
                lst.append(entry)
                existing_codex_hooks[event_name] = lst

            merge_codex("UserPromptSubmit", f'python "{runner_abs}" --provider codex --event UserPromptSubmit --root "{root_abs}"')
            merge_codex("PreToolUse", f'python "{runner_abs}" --provider codex --event PreToolUse --root "{root_abs}"')
            merge_codex("PostToolUse", f'python "{runner_abs}" --provider codex --event PostToolUse --root "{root_abs}"')
            merge_codex("Stop", f'python "{runner_abs}" --provider codex --event Stop --root "{root_abs}"')
            codex_hooks.write_text(json.dumps(existing_codex_hooks, indent=2), encoding="utf-8")
            append_or_create_instructions(target_dir / "CODEX.md", f"# Codex Guidelines\n\n{proto_instructions}", proto_instructions)
            installed_files["codex"] = [".codex/hooks.json", "CODEX.md"]

        elif p_clean in ["cursor"]:
            cursor_dir = target_dir / ".cursor"
            cursor_dir.mkdir(parents=True, exist_ok=True)
            cursor_hooks = cursor_dir / "hooks.json"
            backup_config(cursor_hooks)
            existing_cursor_hooks = read_json_safe(cursor_hooks)

            def merge_cursor(event_name, cmd):
                lst = existing_cursor_hooks.get(event_name, [])
                if not isinstance(lst, list): lst = []
                entry = {"command": cmd}
                for i, existing in enumerate(lst):
                    if "memory_hook_runner.pyc" in str(existing.get("command", "")):
                        lst[i] = entry
                        existing_cursor_hooks[event_name] = lst
                        return
                lst.append(entry)
                existing_cursor_hooks[event_name] = lst

            merge_cursor("beforeSubmitPrompt", f'python "{runner_abs}" --provider cursor --event beforeSubmitPrompt --root "{root_abs}"')
            merge_cursor("preToolUse", f'python "{runner_abs}" --provider cursor --event preToolUse --root "{root_abs}"')
            merge_cursor("postToolUse", f'python "{runner_abs}" --provider cursor --event postToolUse --root "{root_abs}"')
            merge_cursor("stop", f'python "{runner_abs}" --provider cursor --event stop --root "{root_abs}"')
            cursor_hooks.write_text(json.dumps(existing_cursor_hooks, indent=2), encoding="utf-8")
            append_or_create_instructions(target_dir / ".cursorrules", f"# Cursor Rules\n\n{proto_instructions}", proto_instructions)
            installed_files["cursor"] = [".cursor/hooks.json", ".cursorrules"]

    return installed_files


def main():
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="Universal standalone installer for memory-hook.",
    )
    parser.add_argument(
        "target_path",
        nargs="?",
        default=None,
        help="Target directory to install memory-hook into (default: current directory)",
    )
    parser.add_argument(
        "--provider",
        choices=["all", "antigravity", "claude", "codex", "cursor"],
        default="all",
        help="Specific provider to configure (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Display proposed actions without modifying files",
    )
    args = parser.parse_args()

    target_dir = Path(args.target_path).resolve() if args.target_path else Path.cwd().resolve()
    providers = [args.provider] if args.provider != "all" else ["all"]

    prefix = "[DRY-RUN] " if args.dry_run else ""
    print(f"{prefix}Installing universal memory-hook into: {target_dir}")
    if install_hooks is not None:
        installed = install_hooks(target_dir, providers=providers, dry_run=args.dry_run)
    else:
        installed = _standalone_install(target_dir, providers=providers, dry_run=args.dry_run)

    print(f"\n{prefix}✓ Installation {'planned' if args.dry_run else 'successful'}!")
    print("Configured providers:")
    for prov, files in installed.items():
        print(f"  • {prov}: {', '.join(files)}")

    if not args.dry_run:
        print("\nDeterministic logging is active in .memory/short.log")
        print("Agent synthesis knowledge base is in .memory/long.log")


if __name__ == "__main__":
    main()
