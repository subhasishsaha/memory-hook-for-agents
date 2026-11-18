import json
import sys
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
        self.assertTrue((mem_dir / "bin" / "memory_hook_runner.pyc").is_file())
        self.assertFalse((mem_dir / "bin" / "memory_hook_runner.py").exists())

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

    def test_existing_agents_md_preservation_and_append(self):
        # 1. Pre-create an AGENTS.md with original project guidelines
        agents_md = self.root / "AGENTS.md"
        original_content = (
            "# RegioDB Project Guidelines\n\n"
            "## Architecture Rules\n"
            "- Invariant: max_point >= 2 * min_point\n"
            "- Always use pathlib.Path\n"
        )
        agents_md.write_text(original_content, encoding="utf-8")

        # 2. Run install_hooks
        install_hooks(self.root, providers=["antigravity"])

        # 3. Read back and verify original instructions are preserved
        updated = agents_md.read_text(encoding="utf-8")
        self.assertTrue(updated.startswith("# RegioDB Project Guidelines"))
        self.assertIn("- Invariant: max_point >= 2 * min_point", updated)
        self.assertIn("- Always use pathlib.Path", updated)

        # 4. Verify memory hook protocol is appended with separator
        self.assertIn("---", updated)
        self.assertIn("Universal Memory Hook Protocol (.memory/)", updated)

        # 5. Verify no duplicate header inserted
        self.assertNotIn("# Antigravity Agent Guidelines", updated)

        # 6. Re-run install and verify no duplicate protocol appended
        install_hooks(self.root, providers=["antigravity"])
        re_updated = agents_md.read_text(encoding="utf-8")
        self.assertEqual(re_updated.count("Universal Memory Hook Protocol"), 1)

    def test_standalone_install_preserves_existing_agents_md(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("install_mod", Path(__file__).resolve().parent.parent / "install.py")
        install_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(install_mod)

        # Pre-create AGENTS.md
        agents_md = self.root / "AGENTS.md"
        agents_md.write_text("# Custom Instructions\n- Do not delete me.\n", encoding="utf-8")

        # Run standalone install
        install_mod._standalone_install(self.root)

        updated = agents_md.read_text(encoding="utf-8")
        self.assertTrue(updated.startswith("# Custom Instructions"))
        self.assertIn("- Do not delete me.", updated)
        self.assertIn("---", updated)
        self.assertIn("Universal Memory Hook Protocol (.memory/)", updated)
        self.assertEqual(updated.count("Universal Memory Hook Protocol"), 1)

    def test_compiled_runner_and_read_only_protection(self):
        install_hooks(self.root, providers=["antigravity"])
        compiled = self.root / ".memory" / "bin" / "memory_hook_runner.pyc"
        source = self.root / ".memory" / "bin" / "memory_hook_runner.py"

        self.assertTrue(compiled.is_file())
        self.assertFalse(source.exists())

        # Verify executing compiled file works with python
        import subprocess
        proc = subprocess.run(
            [sys.executable, str(compiled), "--event", "PreToolUse", "--provider", "antigravity", "--root", str(self.root)],
            input="{}",
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("allow", proc.stdout)

        # Verify read-only protection blocks modification
        with self.assertRaises(PermissionError):
            with open(compiled, "wb") as f:
                f.write(b"tamper")


if __name__ == "__main__":
    unittest.main()
