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

    def test_package_and_standalone_installer_parity(self):
        """Verifies package installer and standalone fallback installer produce identical provider configurations."""
        import sys
        # Import _standalone_install from install.py
        sys_path_save = list(sys.path)
        try:
            repo_root = Path(__file__).resolve().parent.parent
            if str(repo_root) not in sys.path:
                sys.path.insert(0, str(repo_root))
            from install import _standalone_install

            root_pkg = Path(tempfile.mkdtemp())
            root_std = Path(tempfile.mkdtemp())

            try:
                # Install via package installer
                install_hooks(root_pkg, providers=["all"])
                # Install via standalone fallback installer
                _standalone_install(root_std, providers=["all"])

                # 1. Antigravity parity
                pkg_ag = json.loads((root_pkg / ".agents" / "hooks.json").read_text(encoding="utf-8"))
                std_ag = json.loads((root_std / ".agents" / "hooks.json").read_text(encoding="utf-8"))
                self.assertIn("memory-hook", pkg_ag)
                self.assertIn("memory-hook", std_ag)
                self.assertEqual(list(pkg_ag["memory-hook"].keys()), list(std_ag["memory-hook"].keys()))

                # 2. Claude Code parity
                pkg_claude = json.loads((root_pkg / ".claude" / "settings.json").read_text(encoding="utf-8"))
                std_claude = json.loads((root_std / ".claude" / "settings.json").read_text(encoding="utf-8"))
                self.assertEqual(list(pkg_claude["hooks"].keys()), list(std_claude["hooks"].keys()))

                # 3. Codex parity
                pkg_codex = json.loads((root_pkg / ".codex" / "hooks.json").read_text(encoding="utf-8"))
                std_codex = json.loads((root_std / ".codex" / "hooks.json").read_text(encoding="utf-8"))
                self.assertEqual(list(pkg_codex.keys()), list(std_codex.keys()))

                # 4. Cursor parity
                pkg_cursor = json.loads((root_pkg / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
                std_cursor = json.loads((root_std / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
                self.assertEqual(list(pkg_cursor.keys()), list(std_cursor.keys()))

                # 5. Instructions protocol parity
                self.assertIn("Universal Memory Hook Protocol", (root_pkg / "AGENTS.md").read_text(encoding="utf-8"))
                self.assertIn("Universal Memory Hook Protocol", (root_std / "AGENTS.md").read_text(encoding="utf-8"))
            finally:
                shutil.rmtree(root_pkg, ignore_errors=True)
                shutil.rmtree(root_std, ignore_errors=True)
        finally:
            sys.path = sys_path_save


if __name__ == "__main__":
    unittest.main()
