import json
import tempfile
import unittest
from pathlib import Path

from memory_hook.constants import LONG_LOG_NAME, MEMORY_DIR_NAME, SHORT_LOG_NAME
from memory_hook.installer import install_hooks
from memory_hook.runner import run_hook
from memory_hook.state import MemoryState


class TestCrossProviderHandoff(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        install_hooks(self.root, providers=["all"])
        self.mem_dir = self.root / MEMORY_DIR_NAME

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_handoff_between_antigravity_claude_and_codex(self):
        # 1. Antigravity interaction (Events 1-4)
        run_hook(
            event_hint="PreInvocation",
            provider_hint="antigravity",
            raw_input_data=json.dumps({"conversationId": "agy-1", "lastUserInput": "Build auth service"}),
            project_root=self.root,
        )
        run_hook(
            event_hint="PreToolUse",
            provider_hint="antigravity",
            raw_input_data=json.dumps({
                "conversationId": "agy-1",
                "toolCall": {"id": "c1", "name": "run_command", "args": {"CommandLine": "npm init -y"}},
            }),
            project_root=self.root,
        )
        run_hook(
            event_hint="PostToolUse",
            provider_hint="antigravity",
            raw_input_data=json.dumps({
                "conversationId": "agy-1",
                "toolCall": {"id": "c1", "name": "run_command"},
                "result": "Wrote to package.json",
            }),
            project_root=self.root,
        )
        run_hook(
            event_hint="Stop",
            provider_hint="antigravity",
            raw_input_data=json.dumps({
                "conversationId": "agy-1",
                "response": "Initialized package.json.",
            }),
            project_root=self.root,
        )

        # 2. Switch to Claude Code (Events 5-8)
        run_hook(
            event_hint="UserPromptSubmit",
            provider_hint="claude-code",
            raw_input_data=json.dumps({"sessionId": "claude-1", "prompt": "Install bcrypt and jsonwebtoken"}),
            project_root=self.root,
        )
        run_hook(
            event_hint="PreToolUse",
            provider_hint="claude-code",
            raw_input_data=json.dumps({
                "sessionId": "claude-1",
                "tool_name": "Bash",
                "tool_input": {"command": "npm install bcrypt"},
            }),
            project_root=self.root,
        )
        run_hook(
            event_hint="PostToolUse",
            provider_hint="claude-code",
            raw_input_data=json.dumps({
                "sessionId": "claude-1",
                "tool_name": "Bash",
                "tool_response": "added 1 package",
            }),
            project_root=self.root,
        )
        run_hook(
            event_hint="Stop",
            provider_hint="claude-code",
            raw_input_data=json.dumps({
                "sessionId": "claude-1",
                "last_assistant_message": "Dependencies installed successfully.",
            }),
            project_root=self.root,
        )

        # 3. Switch to Codex (Events 9-10)
        run_hook(
            event_hint="UserPromptSubmit",
            provider_hint="codex",
            raw_input_data=json.dumps({"prompt": "Write auth middleware in src/auth.ts"}),
            project_root=self.root,
        )
        resp_10 = run_hook(
            event_hint="PreToolUse",
            provider_hint="codex",
            raw_input_data=json.dumps({
                "tool": "apply_patch",
                "args": {"path": "src/auth.ts"},
            }),
            project_root=self.root,
        )

        # Verify short.log has all 10 records with exact sequence numbers
        short_log = self.mem_dir / SHORT_LOG_NAME
        with open(short_log, "r", encoding="utf-8") as f:
            lines = [json.loads(l) for l in f.readlines()]

        self.assertEqual(len(lines), 10)
        self.assertEqual([item["seq"] for item in lines], list(range(1, 11)))
        self.assertEqual(lines[0]["provider"], "antigravity")
        self.assertEqual(lines[4]["provider"], "claude-code")
        self.assertEqual(lines[8]["provider"], "codex")

        # 4. Verify Compaction state: after 10 messages, compaction was marked
        state = MemoryState(self.mem_dir)
        self.assertEqual(state.current_seq, 10)
        self.assertEqual(state.last_compacted_seq, 10)

        # 5. Simulate model updating long.log with the synthesized context
        long_log_path = self.mem_dir / LONG_LOG_NAME
        updated_long_log = """# Project Memory & Knowledge Base
> Last Updated: 2026-11-18T00:30:00Z | Provider: codex | Synced Through Event: #10

## 1. Project Overview & Primary Goals
- **Mission**: Authentication microservice built collaboratively across Antigravity, Claude, and Codex.

## 2. Technology Stack & Environment
- **Languages & Runtimes**: Node.js, TypeScript.
- **Key Frameworks & Libraries**: bcrypt, jsonwebtoken.

## 3. Architecture & Core Decisions
- [x] Initialized package.json via Antigravity.
- [x] Installed security dependencies via Claude Code.
- [x] Auth middleware created via Codex.

## 4. Current State & Active Focus
- **Current Task**: Completed initial auth middleware drafting.

## 5. Next Immediate Steps
1. Add token verification tests in Jest.
"""
        with open(long_log_path, "w", encoding="utf-8") as f:
            f.write(updated_long_log)

        # 6. Verify Cursor (or any 4th provider) can inspect long.log and understand state
        with open(long_log_path, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("Authentication microservice", content)
            self.assertIn("bcrypt, jsonwebtoken", content)
            self.assertIn("Synced Through Event: #10", content)


if __name__ == "__main__":
    unittest.main()
