"""Unit tests for non-destructive installer configuration merging."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from memory_hook.installer import install_hooks


class TestInstallerMerge(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.root = Path(self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_non_destructive_merge_antigravity(self):
        agents_dir = self.root / ".agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        hooks_file = agents_dir / "hooks.json"
        # Pre-existing custom user hooks
        initial_hooks = {
            "custom-tool": {
                "PreInvocation": [{"type": "command", "command": "echo custom-linter"}]
            }
        }
        hooks_file.write_text(json.dumps(initial_hooks, indent=2), encoding="utf-8")

        # Run installer
        install_hooks(self.root, providers=["antigravity"])

        # Backup should have been created
        backup_file = agents_dir / "hooks.json.bak"
        self.assertTrue(backup_file.is_file())

        # Verify custom tool is preserved alongside memory-hook
        result_hooks = json.loads(hooks_file.read_text(encoding="utf-8"))
        self.assertIn("custom-tool", result_hooks)
        self.assertEqual(result_hooks["custom-tool"]["PreInvocation"][0]["command"], "echo custom-linter")
        self.assertIn("memory-hook", result_hooks)

    def test_non_destructive_merge_claude(self):
        claude_dir = self.root / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        settings_file = claude_dir / "settings.json"
        # Pre-existing Claude settings with custom hook and other settings
        initial_settings = {
            "model": "claude-3-5-sonnet",
            "hooks": {
                "PreToolUse": [
                    {"matcher": "bash", "hooks": [{"type": "command", "command": "echo check-bash"}]}
                ]
            }
        }
        settings_file.write_text(json.dumps(initial_settings, indent=2), encoding="utf-8")

        install_hooks(self.root, providers=["claude"])

        # Backup should have been created
        backup_file = claude_dir / "settings.json.bak"
        self.assertTrue(backup_file.is_file())

        result = json.loads(settings_file.read_text(encoding="utf-8"))
        self.assertEqual(result["model"], "claude-3-5-sonnet")
        pre_tools = result["hooks"]["PreToolUse"]
        # Both user's custom hook AND memory-hook should be present
        self.assertEqual(len(pre_tools), 2)
        commands = [h["command"] for entry in pre_tools for h in entry.get("hooks", [])]
        self.assertIn("echo check-bash", commands)
        self.assertTrue(any("memory_hook_runner.pyc" in cmd for cmd in commands))

    def test_idempotent_installation_no_duplicate_entries(self):
        install_hooks(self.root, providers=["all"])
        # Run install a second time
        install_hooks(self.root, providers=["all"])

        # Check Claude hooks are not duplicated
        claude_settings = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(len(claude_settings["hooks"]["PreToolUse"]), 1)
        self.assertEqual(len(claude_settings["hooks"]["UserPromptSubmit"]), 1)

    def test_corrupt_config_creates_backup_and_raises(self):
        claude_dir = self.root / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        settings_file = claude_dir / "settings.json"
        settings_file.write_text("{ malformed json ...", encoding="utf-8")

        with self.assertRaises(RuntimeError) as ctx:
            install_hooks(self.root, providers=["claude"])

        self.assertIn("malformed JSON", str(ctx.exception))
        # Corrupt backup should exist
        corrupt_backups = list(claude_dir.glob("settings.json.corrupt.*"))
        self.assertTrue(len(corrupt_backups) > 0)

    def test_dry_run_does_not_modify_disk(self):
        res = install_hooks(self.root, providers=["all"], dry_run=True)
        self.assertIn("antigravity", res)
        # Disk must remain untouched
        self.assertFalse((self.root / ".memory").exists())
        self.assertFalse((self.root / ".agents").exists())


if __name__ == "__main__":
    unittest.main()
