"""Installer module to configure hooks and instructions across providers non-destructively."""

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .constants import (
    BIN_DIR_NAME,
    LONG_LOG_NAME,
    MEMORY_DIR_NAME,
    PROVIDER_ANTIGRAVITY,
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    PROVIDER_CURSOR,
    RUNNER_COMPILED_NAME,
    RUNNER_SCRIPT_NAME,
    RUNNER_SOURCE_NAME,
    SHORT_LOG_NAME,
    STATE_FILE_NAME,
    SUPPORTED_PROVIDERS,
)
from .instructions import (
    AGENT_MEMORY_INSTRUCTIONS,
    LONG_LOG_TEMPLATE,
    get_agents_md_content,
    get_claude_md_content,
    get_codex_md_content,
    get_cursorrules_content,
)
from .standalone_template import STANDALONE_RUNNER_CODE
from .state import MemoryState


def initialize_memory_directory(project_root: Path, dry_run: bool = False) -> Path:
    """Creates .memory directory, initializes short.log, long.log, state.json, and bin/."""
    memory_dir = project_root / MEMORY_DIR_NAME
    bin_dir = memory_dir / BIN_DIR_NAME
    if dry_run:
        return memory_dir

    memory_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)

    # 0. Migrate any legacy .txt files (short.txt, long.txt) to .log
    short_txt = memory_dir / "short.txt"
    short_log = memory_dir / SHORT_LOG_NAME
    if short_txt.is_file():
        if not short_log.is_file():
            try:
                short_txt.rename(short_log)
            except Exception:
                pass
        else:
            try:
                short_txt.unlink()
            except Exception:
                pass

    long_txt = memory_dir / "long.txt"
    long_log = memory_dir / LONG_LOG_NAME
    if long_txt.is_file():
        if not long_log.is_file():
            try:
                long_txt.rename(long_log)
            except Exception:
                pass
        else:
            try:
                long_txt.unlink()
            except Exception:
                pass

    # 1. Initialize short.log if not exists
    if not short_log.exists():
        short_log.touch()

    # 2. Initialize long.log if not exists
    if not long_log.exists():
        now_iso = datetime.now(timezone.utc).isoformat()
        initial_content = LONG_LOG_TEMPLATE.format(
            timestamp=now_iso,
            provider="initial-setup",
            seq=0,
            mission=f"Project root at {project_root.name}",
            use_case="General development with cross-provider AI agents",
            stack="Auto-detected",
            frameworks="Standard library",
        )
        with open(long_log, "w", encoding="utf-8") as f:
            f.write(initial_content)

    # 3. Initialize state.json
    state = MemoryState(memory_dir)

    # 4. Deploy self-contained portable runner script to .memory/bin/
    legacy_source = bin_dir / RUNNER_SOURCE_NAME
    if legacy_source.is_file():
        try:
            legacy_source.unlink()
        except Exception:
            pass

    runner_target = bin_dir / RUNNER_SCRIPT_NAME
    sha256 = generate_standalone_runner(runner_target)
    if sha256:
        state.data["runner_sha256"] = sha256
    state.save()

    return memory_dir


def verify_runner_integrity(memory_dir: Path) -> Tuple[bool, str]:
    """Verifies that installed runner matches recorded SHA-256 hash."""
    state = MemoryState(memory_dir)
    expected_hash = state.data.get("runner_sha256")
    runner_path = memory_dir / BIN_DIR_NAME / RUNNER_COMPILED_NAME
    if not runner_path.is_file():
        return False, "Runner binary missing"
    if not expected_hash:
        return True, "No integrity baseline recorded"

    try:
        with open(runner_path, "rb") as f:
            current_hash = hashlib.sha256(f.read()).hexdigest()
        if current_hash == expected_hash:
            return True, "Integrity verified (SHA-256 match)"
        return False, f"Integrity mismatch (expected {expected_hash[:8]}..., found {current_hash[:8]}...)"
    except Exception as e:
        return False, f"Integrity check error: {e}"


def generate_standalone_runner(target_path: Path) -> Optional[str]:
    """Generates a zero-dependency, self-contained Python runner in .memory/bin/ and returns SHA-256."""
    runner_code = STANDALONE_RUNNER_CODE
    bin_dir = target_path.parent
    compiled_path = bin_dir / RUNNER_COMPILED_NAME
    temp_source_path = bin_dir / RUNNER_SOURCE_NAME

    if compiled_path.exists():
        try:
            if os.name == "nt":
                import subprocess
                subprocess.run(["attrib", "-R", str(compiled_path)], check=False, capture_output=True)
            else:
                compiled_path.chmod(0o755)
        except Exception:
            pass

    with open(temp_source_path, "w", encoding="utf-8") as f:
        f.write(runner_code)

    import py_compile
    py_compile.compile(str(temp_source_path), cfile=str(compiled_path), doraise=True)

    try:
        temp_source_path.unlink()
    except Exception:
        pass

    sha256 = None
    try:
        with open(compiled_path, "rb") as f:
            sha256 = hashlib.sha256(f.read()).hexdigest()
    except Exception:
        pass

    try:
        if os.name == "nt":
            import subprocess
            subprocess.run(["attrib", "+R", str(compiled_path)], check=False, capture_output=True)
        else:
            compiled_path.chmod(0o555)
    except Exception:
        pass

    return sha256


def safe_backup_file(file_path: Path) -> Optional[Path]:
    """Creates a timestamped backup before modifying a configuration file."""
    if not file_path.is_file():
        return None
    backup_path = file_path.with_name(f"{file_path.name}.bak")
    try:
        shutil.copy2(file_path, backup_path)
        return backup_path
    except Exception:
        return None


def read_json_safely(file_path: Path) -> Dict[str, Any]:
    """Reads a JSON file safely. If corrupt, creates a corrupt backup and raises RuntimeError."""
    if not file_path.is_file():
        return {}
    content = file_path.read_text(encoding="utf-8").strip()
    if not content:
        return {}
    try:
        loaded = json.loads(content)
        if isinstance(loaded, dict):
            return loaded
        raise ValueError("JSON root must be an object")
    except Exception as e:
        now_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        corrupt_backup = file_path.with_name(f"{file_path.name}.corrupt.{now_ts}")
        try:
            shutil.copy2(file_path, corrupt_backup)
        except Exception:
            pass
        raise RuntimeError(
            f"Existing config {file_path} is malformed JSON ({e}). "
            f"Preserved damaged backup to {corrupt_backup.name}."
        )


def append_or_create_instructions(
    file_path: Path,
    full_new_content: str,
    append_protocol: str = AGENT_MEMORY_INSTRUCTIONS,
    dry_run: bool = False,
) -> None:
    """Creates instruction file or appends memory protocol at the bottom as separate section."""
    if dry_run:
        return

    if not file_path.exists():
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(full_new_content.strip() + "\n")
    else:
        with open(file_path, "r", encoding="utf-8") as f:
            existing = f.read()
        if "Universal Memory Hook Protocol" not in existing:
            separator = "\n\n---\n\n" if existing.strip() else ""
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(separator + append_protocol.strip() + "\n")


def quote_if_needed(path_str: str) -> str:
    """Wraps path in double quotes only if it contains whitespace."""
    return f'"{path_str}"' if " " in path_str else path_str


def install_antigravity(project_root: Path, dry_run: bool = False) -> List[Path]:
    """Configures Antigravity .agents/hooks.json and AGENTS.md non-destructively."""
    agents_dir = project_root / ".agents"
    hooks_file = agents_dir / "hooks.json"
    agents_md = project_root / "AGENTS.md"

    if dry_run:
        return [hooks_file, agents_md]

    agents_dir.mkdir(parents=True, exist_ok=True)
    root_abs = project_root.resolve().as_posix()
    runner_abs = (project_root / ".memory" / "bin" / RUNNER_SCRIPT_NAME).resolve().as_posix()
    r_arg = quote_if_needed(runner_abs)
    rt_arg = quote_if_needed(root_abs)

    safe_backup_file(hooks_file)
    existing_hooks = read_json_safely(hooks_file)

    memory_hook_config = {
        "PreInvocation": [
            {
                "type": "command",
                "command": f'python {r_arg} --provider antigravity --event PreInvocation --root {rt_arg}'
            }
        ],
        "PreToolUse": [
            {
                "matcher": ".*",
                "hooks": [
                    {
                        "type": "command",
                        "command": f'python {r_arg} --provider antigravity --event PreToolUse --root {rt_arg}'
                    }
                ]
            }
        ],
        "PostToolUse": [
            {
                "matcher": ".*",
                "hooks": [
                    {
                        "type": "command",
                        "command": f'python {r_arg} --provider antigravity --event PostToolUse --root {rt_arg}'
                    }
                ]
            }
        ],
        "Stop": [
            {
                "type": "command",
                "command": f'python {r_arg} --provider antigravity --event Stop --root {rt_arg}'
            }
        ]
    }

    # Non-destructive: merge into existing hooks
    existing_hooks["memory-hook"] = memory_hook_config
    with open(hooks_file, "w", encoding="utf-8") as f:
        json.dump(existing_hooks, f, indent=2, ensure_ascii=False)

    append_or_create_instructions(agents_md, get_agents_md_content())
    return [hooks_file, agents_md]


def install_claude(project_root: Path, dry_run: bool = False) -> List[Path]:
    """Configures Claude Code .claude/settings.json and CLAUDE.md non-destructively."""
    claude_dir = project_root / ".claude"
    settings_file = claude_dir / "settings.json"
    claude_md = project_root / "CLAUDE.md"

    if dry_run:
        return [settings_file, claude_md]

    claude_dir.mkdir(parents=True, exist_ok=True)
    root_abs = project_root.resolve().as_posix()
    runner_abs = (project_root / ".memory" / "bin" / RUNNER_SCRIPT_NAME).resolve().as_posix()

    safe_backup_file(settings_file)
    existing_settings = read_json_safely(settings_file)

    hooks = existing_settings.get("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}

    def merge_hook_list(event_name: str, cmd_str: str, matcher: str = "") -> None:
        entries = hooks.get(event_name, [])
        if not isinstance(entries, list):
            entries = []
        # Check if memory-hook entry already exists
        found = False
        new_entry = {
            "matcher": matcher,
            "hooks": [{"type": "command", "command": cmd_str}]
        }
        for idx, entry in enumerate(entries):
            if isinstance(entry, dict) and "hooks" in entry:
                for sub_hook in entry.get("hooks", []):
                    if "memory_hook_runner.pyc" in str(sub_hook.get("command", "")) or "memory-hook" in str(sub_hook.get("command", "")):
                        entries[idx] = new_entry
                        found = True
                        break
            if found:
                break
        if not found:
            entries.append(new_entry)
        hooks[event_name] = entries

    merge_hook_list(
        "UserPromptSubmit",
        f'python "{runner_abs}" --provider claude-code --event UserPromptSubmit --root "{root_abs}"',
        matcher=""
    )
    merge_hook_list(
        "PreToolUse",
        f'python "{runner_abs}" --provider claude-code --event PreToolUse --root "{root_abs}"',
        matcher=".*"
    )
    merge_hook_list(
        "PostToolUse",
        f'python "{runner_abs}" --provider claude-code --event PostToolUse --root "{root_abs}"',
        matcher=".*"
    )
    merge_hook_list(
        "Stop",
        f'python "{runner_abs}" --provider claude-code --event Stop --root "{root_abs}"',
        matcher=""
    )

    existing_settings["hooks"] = hooks
    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump(existing_settings, f, indent=2, ensure_ascii=False)

    append_or_create_instructions(claude_md, get_claude_md_content())
    return [settings_file, claude_md]


def install_codex(project_root: Path, dry_run: bool = False) -> List[Path]:
    """Configures OpenAI Codex .codex/hooks.json and CODEX.md non-destructively."""
    codex_dir = project_root / ".codex"
    hooks_file = codex_dir / "hooks.json"
    codex_md = project_root / "CODEX.md"

    if dry_run:
        return [hooks_file, codex_md]

    codex_dir.mkdir(parents=True, exist_ok=True)
    root_abs = project_root.resolve().as_posix()
    runner_abs = (project_root / ".memory" / "bin" / RUNNER_SCRIPT_NAME).resolve().as_posix()

    safe_backup_file(hooks_file)
    existing_hooks = read_json_safely(hooks_file)

    def merge_simple_hook(event_name: str, cmd_str: str) -> None:
        entries = existing_hooks.get(event_name, [])
        if not isinstance(entries, list):
            entries = []
        found = False
        new_entry = {"type": "command", "command": cmd_str}
        for idx, entry in enumerate(entries):
            if isinstance(entry, dict) and ("memory_hook_runner.pyc" in str(entry.get("command", "")) or "memory-hook" in str(entry.get("command", ""))):
                entries[idx] = new_entry
                found = True
                break
        if not found:
            entries.append(new_entry)
        existing_hooks[event_name] = entries

    merge_simple_hook("UserPromptSubmit", f'python "{runner_abs}" --provider codex --event UserPromptSubmit --root "{root_abs}"')
    merge_simple_hook("PreToolUse", f'python "{runner_abs}" --provider codex --event PreToolUse --root "{root_abs}"')
    merge_simple_hook("PostToolUse", f'python "{runner_abs}" --provider codex --event PostToolUse --root "{root_abs}"')
    merge_simple_hook("Stop", f'python "{runner_abs}" --provider codex --event Stop --root "{root_abs}"')

    with open(hooks_file, "w", encoding="utf-8") as f:
        json.dump(existing_hooks, f, indent=2, ensure_ascii=False)

    append_or_create_instructions(codex_md, get_codex_md_content())
    return [hooks_file, codex_md]


def install_cursor(project_root: Path, dry_run: bool = False) -> List[Path]:
    """Configures Cursor .cursor/hooks.json and .cursorrules non-destructively."""
    cursor_dir = project_root / ".cursor"
    hooks_file = cursor_dir / "hooks.json"
    cursorrules = project_root / ".cursorrules"

    if dry_run:
        return [hooks_file, cursorrules]

    cursor_dir.mkdir(parents=True, exist_ok=True)
    root_abs = project_root.resolve().as_posix()
    runner_abs = (project_root / ".memory" / "bin" / RUNNER_SCRIPT_NAME).resolve().as_posix()

    safe_backup_file(hooks_file)
    existing_hooks = read_json_safely(hooks_file)

    def merge_cursor_hook(event_name: str, cmd_str: str) -> None:
        entries = existing_hooks.get(event_name, [])
        if not isinstance(entries, list):
            entries = []
        found = False
        new_entry = {"command": cmd_str}
        for idx, entry in enumerate(entries):
            if isinstance(entry, dict) and ("memory_hook_runner.pyc" in str(entry.get("command", "")) or "memory-hook" in str(entry.get("command", ""))):
                entries[idx] = new_entry
                found = True
                break
        if not found:
            entries.append(new_entry)
        existing_hooks[event_name] = entries

    merge_cursor_hook("beforeSubmitPrompt", f'python "{runner_abs}" --provider cursor --event beforeSubmitPrompt --root "{root_abs}"')
    merge_cursor_hook("preToolUse", f'python "{runner_abs}" --provider cursor --event preToolUse --root "{root_abs}"')
    merge_cursor_hook("postToolUse", f'python "{runner_abs}" --provider cursor --event postToolUse --root "{root_abs}"')
    merge_cursor_hook("stop", f'python "{runner_abs}" --provider cursor --event stop --root "{root_abs}"')

    with open(hooks_file, "w", encoding="utf-8") as f:
        json.dump(existing_hooks, f, indent=2, ensure_ascii=False)

    append_or_create_instructions(cursorrules, get_cursorrules_content())
    return [hooks_file, cursorrules]


def install_hooks(
    project_root: Optional[Path] = None,
    providers: Optional[List[str]] = None,
    dry_run: bool = False,
) -> Dict[str, List[str]]:
    """Installs memory-hook into the designated project repository non-destructively."""
    root = (project_root or Path.cwd()).resolve()
    initialize_memory_directory(root, dry_run=dry_run)

    installed_files: Dict[str, List[str]] = {}
    targets = providers or ["all"]
    if "all" in targets:
        targets = [PROVIDER_ANTIGRAVITY, PROVIDER_CLAUDE, PROVIDER_CODEX, PROVIDER_CURSOR]

    for p in targets:
        p_clean = p.lower().strip()
        if p_clean in [PROVIDER_ANTIGRAVITY, "gemini"]:
            files = install_antigravity(root, dry_run=dry_run)
            installed_files[PROVIDER_ANTIGRAVITY] = [str(f.relative_to(root)) for f in files]
        elif p_clean in [PROVIDER_CLAUDE, "claude"]:
            files = install_claude(root, dry_run=dry_run)
            installed_files[PROVIDER_CLAUDE] = [str(f.relative_to(root)) for f in files]
        elif p_clean in [PROVIDER_CODEX, "openai"]:
            files = install_codex(root, dry_run=dry_run)
            installed_files[PROVIDER_CODEX] = [str(f.relative_to(root)) for f in files]
        elif p_clean in [PROVIDER_CURSOR, "cursor"]:
            files = install_cursor(root, dry_run=dry_run)
            installed_files[PROVIDER_CURSOR] = [str(f.relative_to(root)) for f in files]

    return installed_files
