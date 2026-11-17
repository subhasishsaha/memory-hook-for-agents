import json
import tempfile
import unittest
from pathlib import Path

from memory_hook.constants import LONG_LOG_NAME, MEMORY_DIR_NAME, SHORT_LOG_NAME, STATE_FILE_NAME
from memory_hook.installer import install_hooks


class TestInstaller(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_full_installation(self):
        installed = install_hooks(self.root, providers=["all"])

        # Check .memory directory
        mem_dir = self.root / MEMORY_DIR_NAME
        self.assertTrue(mem_dir.is_dir())
        self.assertTrue((mem_dir / SHORT_LOG_NAME).is_file())
        self.assertTrue((mem_dir / LONG_LOG_NAME).is_file())
        self.assertTrue((mem_dir / STATE_FILE_NAME).is_file())
        self.assertTrue((mem_dir / "bin" / "memory_hook_runner.py").is_file())

        # Check Antigravity files
        hooks_json = self.root / ".agents" / "hooks.json"
        agents_md = self.root / "AGENTS.md"
        self.assertTrue(hooks_json.is_file())
        self.assertTrue(agents_md.is_file())
        with open(hooks_json, "r", encoding="utf-8") as f:
            hooks = json.load(f)
            self.assertIn("memory-hook", hooks)
            self.assertIn("PreInvocation", hooks["memory-hook"])
            self.assertIn("PreToolUse", hooks["memory-hook"])
            cmd = hooks["memory-hook"]["PreInvocation"][0]["command"]
            self.assertIn(self.root.resolve().as_posix(), cmd)
            self.assertIn("--root", cmd)

        # Check Claude Code files
        settings_json = self.root / ".claude" / "settings.json"
        claude_md = self.root / "CLAUDE.md"
        self.assertTrue(settings_json.is_file())
        self.assertTrue(claude_md.is_file())
        with open(settings_json, "r", encoding="utf-8") as f:
            settings = json.load(f)
            self.assertIn("hooks", settings)
            self.assertIn("UserPromptSubmit", settings["hooks"])

        # Check Codex files
        codex_json = self.root / ".codex" / "hooks.json"
        codex_md = self.root / "CODEX.md"
        self.assertTrue(codex_json.is_file())
        self.assertTrue(codex_md.is_file())

        # Check Cursor files
        cursor_json = self.root / ".cursor" / "hooks.json"
        cursorrules = self.root / ".cursorrules"
        self.assertTrue(cursor_json.is_file())
        self.assertTrue(cursorrules.is_file())


if __name__ == "__main__":
    unittest.main()
