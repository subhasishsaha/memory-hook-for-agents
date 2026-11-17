import unittest
from memory_hook.constants import (
    EVENT_MODEL_RESPONSE,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_USER_REQUEST,
    PROVIDER_ANTIGRAVITY,
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    PROVIDER_CURSOR,
)
from memory_hook.normalizer import detect_provider, normalize_event, truncate_string


class TestNormalizer(unittest.TestCase):
    def test_truncate_string(self):
        short_str = "hello world"
        res, was_trunc = truncate_string(short_str, max_len=20)
        self.assertEqual(res, "hello world")
        self.assertFalse(was_trunc)

        long_str = "a" * 100
        res, was_trunc = truncate_string(long_str, max_len=10)
        self.assertTrue(was_trunc)
        self.assertTrue(res.startswith("aaaaaaaaaa\n... [truncated 90"))

    def test_detect_provider(self):
        self.assertEqual(detect_provider({}, hint="claude-code"), PROVIDER_CLAUDE)
        self.assertEqual(
            detect_provider({"conversationId": "123", "artifactDirectoryPath": "/tmp"}),
            PROVIDER_ANTIGRAVITY,
        )
        self.assertEqual(
            detect_provider({"sessionId": "abc", "prompt": "test"}),
            PROVIDER_CLAUDE,
        )

    def test_normalize_antigravity_pre_invocation(self):
        raw = {
            "conversationId": "conv-1",
            "lastUserInput": "Build a REST API",
            "hookEventName": "PreInvocation",
            "invocationNum": 1,
        }
        event = normalize_event(raw, event_hint="PreInvocation", provider_hint=PROVIDER_ANTIGRAVITY)
        self.assertEqual(event["type"], EVENT_USER_REQUEST)
        self.assertEqual(event["provider"], PROVIDER_ANTIGRAVITY)
        self.assertEqual(event["session_id"], "conv-1")
        self.assertEqual(event["payload"]["content"], "Build a REST API")
        self.assertFalse(event["truncated"])

    def test_normalize_antigravity_pre_tool_use(self):
        raw = {
            "conversationId": "conv-1",
            "toolCall": {
                "id": "tool_123",
                "name": "run_command",
                "args": {"CommandLine": "npm test"},
            },
            "stepIdx": 5,
        }
        event = normalize_event(raw, event_hint="PreToolUse", provider_hint=PROVIDER_ANTIGRAVITY)
        self.assertEqual(event["type"], EVENT_TOOL_CALL)
        self.assertEqual(event["payload"]["tool_name"], "run_command")
        self.assertEqual(event["payload"]["call_id"], "tool_123")
        self.assertEqual(event["payload"]["args"]["CommandLine"], "npm test")

    def test_normalize_antigravity_post_tool_use(self):
        raw = {
            "conversationId": "conv-1",
            "toolCall": {"id": "tool_123", "name": "run_command"},
            "result": "Tests passed: 10/10",
            "error": None,
        }
        event = normalize_event(raw, event_hint="PostToolUse", provider_hint=PROVIDER_ANTIGRAVITY)
        self.assertEqual(event["type"], EVENT_TOOL_RESULT)
        self.assertEqual(event["payload"]["status"], "success")
        self.assertEqual(event["payload"]["content"], "Tests passed: 10/10")

    def test_normalize_claude_events(self):
        # Prompt
        raw_prompt = {"prompt": "Fix bug in auth", "sessionId": "sess-claude"}
        event = normalize_event(raw_prompt, event_hint="UserPromptSubmit", provider_hint=PROVIDER_CLAUDE)
        self.assertEqual(event["type"], EVENT_USER_REQUEST)
        self.assertEqual(event["payload"]["content"], "Fix bug in auth")

        # Tool use
        raw_tool = {
            "sessionId": "sess-claude",
            "tool_name": "Edit",
            "tool_use_id": "tu-1",
            "tool_input": {"path": "auth.js"},
        }
        event_tool = normalize_event(raw_tool, event_hint="PreToolUse", provider_hint=PROVIDER_CLAUDE)
        self.assertEqual(event_tool["type"], EVENT_TOOL_CALL)
        self.assertEqual(event_tool["payload"]["tool_name"], "Edit")

        # Tool post
        raw_post = {
            "sessionId": "sess-claude",
            "tool_name": "Edit",
            "tool_use_id": "tu-1",
            "tool_response": "File edited successfully",
        }
        event_post = normalize_event(raw_post, event_hint="PostToolUse", provider_hint=PROVIDER_CLAUDE)
        self.assertEqual(event_post["type"], EVENT_TOOL_RESULT)
        self.assertEqual(event_post["payload"]["status"], "success")


if __name__ == "__main__":
    unittest.main()
