import json
import tempfile
import unittest
from pathlib import Path

from memory_hook.constants import MEMORY_DIR_NAME, SHORT_LOG_NAME, STATE_FILE_NAME
from memory_hook.runner import run_hook
from memory_hook.state import MemoryState


class TestRunner(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.mem_dir = self.root / MEMORY_DIR_NAME

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_single_hook_run(self):
        payload = json.dumps({
            "conversationId": "test-conv-1",
            "lastUserInput": "Hello agent",
        })
        resp = run_hook(
            event_hint="PreInvocation",
            provider_hint="antigravity",
            raw_input_data=payload,
            project_root=self.root,
        )

        short_log = self.mem_dir / SHORT_LOG_NAME
        self.assertTrue(short_log.exists())
        with open(short_log, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["seq"], 1)
        self.assertEqual(record["payload"]["content"], "Hello agent")
        self.assertEqual(record["provider"], "antigravity")

    def test_compaction_trigger_after_10_messages(self):
        # Fire 9 tool calls
        for i in range(9):
            payload = json.dumps({
                "conversationId": "test-conv-1",
                "toolCall": {"id": f"call_{i}", "name": "read_file", "args": {"path": "a.txt"}},
                "stepIdx": i,
            })
            resp = run_hook(
                event_hint="PreToolUse",
                provider_hint="antigravity",
                raw_input_data=payload,
                project_root=self.root,
            )
            self.assertEqual(resp.get("decision"), "allow")

        # 10th event: PreInvocation where compaction notice can be injected
        payload_10 = json.dumps({
            "conversationId": "test-conv-1",
            "lastUserInput": "Next step please",
            "invocationNum": 2,
        })
        resp_10 = run_hook(
            event_hint="PreInvocation",
            provider_hint="antigravity",
            raw_input_data=payload_10,
            project_root=self.root,
        )

        # Should inject compaction directive
        self.assertIn("injectSteps", resp_10)
        injected_msg = resp_10["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIn("[MEMORY HOOK UPDATE NOTICE]", injected_msg)
        self.assertIn("10 actions have elapsed", injected_msg)

        # State should now be marked compacted
        state = MemoryState(self.mem_dir)
        self.assertEqual(state.last_compacted_seq, 10)
        self.assertEqual(state.current_seq, 10)


if __name__ == "__main__":
    unittest.main()
