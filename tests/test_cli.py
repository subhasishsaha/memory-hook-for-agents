"""Unit tests for memory-hook CLI subcommands (install, status, inspect, run)."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memory_hook.cli import cmd_inspect, cmd_install, cmd_run, cmd_status, main
from memory_hook.constants import (
    LONG_LOG_NAME,
    MEMORY_DIR_NAME,
    SHORT_LOG_NAME,
    STATE_FILE_NAME,
)


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.target_path = Path(self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_install_subcommand(self):
        """Tests 'install' subcommand creates required directories and files."""
        test_args = ["memory-hook", "install", "--path", str(self.target_path)]
        with patch.object(sys, "argv", test_args):
            main()

        mem_dir = self.target_path / MEMORY_DIR_NAME
        self.assertTrue(mem_dir.is_dir())
        self.assertTrue((mem_dir / SHORT_LOG_NAME).is_file())
        self.assertTrue((mem_dir / LONG_LOG_NAME).is_file())
        self.assertTrue((mem_dir / STATE_FILE_NAME).is_file())
        self.assertTrue((mem_dir / "bin" / "memory_hook_runner.pyc").is_file())
        self.assertFalse((mem_dir / "bin" / "memory_hook_runner.py").exists())

        # Verify all provider configs created
        self.assertTrue((self.target_path / ".agents" / "hooks.json").is_file())
        self.assertTrue((self.target_path / "AGENTS.md").is_file())
        self.assertTrue((self.target_path / ".claude" / "settings.json").is_file())
        self.assertTrue((self.target_path / "CLAUDE.md").is_file())
        self.assertTrue((self.target_path / ".codex" / "hooks.json").is_file())
        self.assertTrue((self.target_path / "CODEX.md").is_file())
        self.assertTrue((self.target_path / ".cursor" / "hooks.json").is_file())
        self.assertTrue((self.target_path / ".cursorrules").is_file())

    def test_install_positional_path(self):
        """Tests 'install' with positional path argument."""
        test_args = ["memory-hook", "install", str(self.target_path)]
        with patch.object(sys, "argv", test_args):
            main()
        self.assertTrue((self.target_path / MEMORY_DIR_NAME).is_dir())

    def test_install_single_provider(self):
        """Tests 'install' with --provider flag."""
        test_args = ["memory-hook", "install", "--path", str(self.target_path), "--provider", "claude"]
        with patch.object(sys, "argv", test_args):
            main()

        self.assertTrue((self.target_path / ".claude" / "settings.json").is_file())
        self.assertTrue((self.target_path / "CLAUDE.md").is_file())
        self.assertFalse((self.target_path / ".agents" / "hooks.json").exists())

    def test_status_uninitialized(self):
        """Tests 'status' in an uninitialized directory exits with error."""
        test_args = ["memory-hook", "status", "--path", str(self.target_path)]
        with patch.object(sys, "argv", test_args):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 1)

    def test_status_initialized(self):
        """Tests 'status' prints expected counters after installation."""
        # Install first
        with patch.object(sys, "argv", ["memory-hook", "install", "--path", str(self.target_path)]):
            main()

        # Capture status output
        captured_out = io.StringIO()
        with patch.object(sys, "argv", ["memory-hook", "status", "--path", str(self.target_path)]):
            with patch("sys.stdout", captured_out):
                main()

        output = captured_out.getvalue()
        self.assertIn("Memory Hook Status", output)
        self.assertIn("Current Sequence:", output)
        self.assertIn("Total Logged Events:", output)

    def test_inspect_empty(self):
        """Tests 'inspect' when no events have been logged."""
        captured_out = io.StringIO()
        with patch.object(sys, "argv", ["memory-hook", "inspect", "--path", str(self.target_path)]):
            with patch("sys.stdout", captured_out):
                main()
        output = captured_out.getvalue()
        self.assertIn("No short.log found", output)

    def test_inspect_with_events(self):
        """Tests 'inspect' correctly prints logged events."""
        with patch.object(sys, "argv", ["memory-hook", "install", "--path", str(self.target_path)]):
            main()

        short_log = self.target_path / MEMORY_DIR_NAME / SHORT_LOG_NAME
        event = {"seq": 1, "ts": "2026-11-18T00:00:00Z", "provider": "test", "type": "user_request", "payload": "hello"}
        short_log.write_text(json.dumps(event) + "\n", encoding="utf-8")

        captured_out = io.StringIO()
        with patch.object(sys, "argv", ["memory-hook", "inspect", "--path", str(self.target_path)]):
            with patch("sys.stdout", captured_out):
                main()

        output = captured_out.getvalue()
        self.assertIn("Showing last", output)
        self.assertIn("hello", output)

    def test_inspect_long(self):
        """Tests 'inspect --long' displays long.log content."""
        with patch.object(sys, "argv", ["memory-hook", "install", "--path", str(self.target_path)]):
            main()

        captured_out = io.StringIO()
        with patch.object(sys, "argv", ["memory-hook", "inspect", "--path", str(self.target_path), "--long"]):
            with patch("sys.stdout", captured_out):
                main()

        output = captured_out.getvalue()
        self.assertIn("Project Memory & Knowledge Base", output)

    def test_run_subcommand(self):
        """Tests 'run' subcommand processes input and writes output."""
        with patch.object(sys, "argv", ["memory-hook", "install", "--path", str(self.target_path)]):
            main()

        fake_stdin = io.StringIO('{"conversationId": "test-123", "lastUserInput": "Build a widget"}')
        captured_stdout = io.StringIO()

        test_args = [
            "memory-hook", "run",
            "--provider", "antigravity",
            "--event", "PreInvocation",
            "--root", str(self.target_path)
        ]
        with patch.object(sys, "argv", test_args):
            with patch("sys.stdin", fake_stdin):
                with patch("sys.stdout", captured_stdout):
                    main()

        output = captured_stdout.getvalue()
        parsed_resp = json.loads(output)
        self.assertIsInstance(parsed_resp, dict)

        # Check short.log recorded the event
        short_log = self.target_path / MEMORY_DIR_NAME / SHORT_LOG_NAME
        self.assertTrue(short_log.is_file())
        with open(short_log, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["seq"], 1)
        self.assertEqual(record["provider"], "antigravity")

    def test_run_via_subprocess(self):
        """Adversarial test executing 'python -m memory_hook.cli run' as a real subprocess."""
        input_data = '{"conversationId": "subproc-test", "tool_name": "bash", "toolCall": {"cmd": "ls"}}'
        proc = subprocess.run(
            [
                sys.executable, "-m", "memory_hook.cli",
                "run",
                "--provider", "claude",
                "--event", "PreToolUse",
                "--root", str(self.target_path)
            ],
            input=input_data,
            capture_output=True,
            text=True
        )
        self.assertEqual(proc.returncode, 0)
        resp = json.loads(proc.stdout)
        self.assertEqual(resp.get("status"), "ok")


if __name__ == "__main__":
    unittest.main()
