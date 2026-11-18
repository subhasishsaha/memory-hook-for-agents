"""Constants and configurations for the memory-hook system."""

import os
from pathlib import Path

MEMORY_DIR_NAME = ".memory"
SHORT_LOG_NAME = "short.log"
LONG_LOG_NAME = "long.log"
STATE_FILE_NAME = "state.json"
BIN_DIR_NAME = "bin"
RUNNER_SCRIPT_NAME = "memory_hook_runner.py"

DEFAULT_COMPACT_INTERVAL = 10  # Legacy event-count fallback
DEFAULT_COMPACT_TOKEN_INTERVAL = 10000  # Trigger long.log compaction notice every 10k tokens
MAX_SHORT_LOG_TOKENS = 100000  # Rolling context window cap for short.log (100k tokens)
CHARS_PER_TOKEN = 4  # Standard text/code token estimation ratio (1 token ~= 4 chars)

# Canonical Event Types in short.log
EVENT_USER_REQUEST = "user_request"
EVENT_TOOL_CALL = "tool_call"
EVENT_TOOL_RESULT = "tool_result"
EVENT_MODEL_RESPONSE = "model_response"

# Supported Providers
PROVIDER_ANTIGRAVITY = "antigravity"
PROVIDER_CLAUDE = "claude-code"
PROVIDER_CODEX = "codex"
PROVIDER_CURSOR = "cursor"
PROVIDER_GENERIC = "generic"

SUPPORTED_PROVIDERS = [
    PROVIDER_ANTIGRAVITY,
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    PROVIDER_CURSOR,
    PROVIDER_GENERIC,
]

# Max length before string payload truncation (to prevent short.log blowing up on massive file reads)
MAX_PAYLOAD_STRING_LENGTH = 16384
