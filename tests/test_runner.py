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

    def test_compaction_trigger_after_10k_tokens(self):
        # Fire 9 tool calls, each carrying ~1,500 tokens (total ~13,500 tokens)
        for i in range(9):
            payload = json.dumps({
                "conversationId": "test-conv-1",
                "toolCall": {
                    "id": f"call_{i}",
                    "name": "write_file",
                    "args": {"path": f"file_{i}.txt", "content": "A" * 6000},
                },
                "stepIdx": i,
            })
            resp = run_hook(
                event_hint="PreToolUse",
                provider_hint="antigravity",
                raw_input_data=payload,
                project_root=self.root,
            )
            self.assertEqual(resp.get("decision"), "allow")

        # 10th event: PreInvocation where compaction notice is injected
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
        self.assertIn("tokens have accumulated", injected_msg)

        # Under the acknowledgement protocol:
        # Notice is emitted and request is marked pending, but checkpoint does not advance yet
        state = MemoryState(self.mem_dir)
        self.assertTrue(state.has_pending_compaction())
        self.assertEqual(state.pending_compaction["status"], "pending")
        req_seq = state.pending_compaction["requested_at_seq"]
        self.assertGreaterEqual(req_seq, 1)
        self.assertEqual(state.last_compacted_seq, 0)
        self.assertEqual(state.current_seq, 10)

        # Now simulate agent/host acknowledging compaction at requested checkpoint
        state.acknowledge_compaction(req_seq)
        self.assertFalse(state.has_pending_compaction())
        self.assertEqual(state.last_compacted_seq, req_seq)
        self.assertGreaterEqual(state.last_compacted_tokens, 10000)

    def test_legacy_txt_migration_to_log(self):
        # Create legacy short.txt and long.txt
        self.mem_dir.mkdir(parents=True, exist_ok=True)
        short_txt = self.mem_dir / "short.txt"
        long_txt = self.mem_dir / "long.txt"
        short_txt.write_text('{"seq": 1, "tokens": 10}\n', encoding="utf-8")
        long_txt.write_text("# Old Long Memory\n", encoding="utf-8")

        # Running hook should automatically migrate .txt files to .log
        run_hook(
            event_hint="PreInvocation",
            provider_hint="antigravity",
            raw_input_data=json.dumps({"prompt": "Hello"}),
            project_root=self.root,
        )

        self.assertFalse(short_txt.exists())
        self.assertFalse(long_txt.exists())
        short_log = self.mem_dir / SHORT_LOG_NAME
        long_log = self.mem_dir / "long.log"
        self.assertTrue(short_log.exists())
        self.assertTrue(long_log.exists())
        self.assertIn("# Old Long Memory", long_log.read_text(encoding="utf-8"))

    def test_short_log_pruning_100k_token_window(self):
        from memory_hook.runner import prune_short_log

        self.mem_dir.mkdir(parents=True, exist_ok=True)
        short_log = self.mem_dir / SHORT_LOG_NAME
        state = MemoryState(self.mem_dir)

        # Generate 15 records, each with ~10k tokens (total ~150k tokens)
        lines = []
        for i in range(1, 16):
            rec = {
                "seq": i,
                "ts": "2026-11-18T00:00:00Z",
                "provider": "antigravity",
                "type": "user_request",
                "tokens": 10000,
                "payload": {"text": f"payload_{i}"},
            }
            lines.append(json.dumps(rec) + "\n")

        short_log.write_text("".join(lines), encoding="utf-8")
        state.data["short_log_tokens"] = 150000
        state.data["current_seq"] = 15
        state.data["last_compacted_seq"] = 15  # All events are compacted history
        state.save()

        # Prune with 100k token limit
        prune_short_log(self.mem_dir, state, max_tokens=100000)

        # Should keep the newest 10 records (10 * 10k = 100k)
        remaining = short_log.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(remaining), 10)
        first_rec = json.loads(remaining[0])
        last_rec = json.loads(remaining[-1])
        self.assertEqual(first_rec["seq"], 6)
        self.assertEqual(last_rec["seq"], 15)
        self.assertEqual(state.short_log_tokens, 100000)
        self.assertEqual(state.window_start_seq, 6)


if __name__ == "__main__":
    unittest.main()
