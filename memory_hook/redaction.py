"""Secret redaction and capture policy for sensitive data."""

import re
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

# Precompiled regex patterns for common API keys, tokens, and secrets
REDACTION_PATTERNS = [
    # Private Key blocks
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        "[REDACTED:PRIVATE_KEY]",
    ),
    # OpenAI / Anthropic-style sk- keys
    (
        re.compile(r"\bsk-[a-zA-Z0-9_\-]{20,}\b"),
        "[REDACTED:API_KEY]",
    ),
    # GitHub Tokens (classic and fine-grained)
    (
        re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36}\b"),
        "[REDACTED:GITHUB_TOKEN]",
    ),
    (
        re.compile(r"\bgithub_pat_[a-zA-Z0-9_]{20,}\b"),
        "[REDACTED:GITHUB_PAT]",
    ),
    # Google API / Gemini / Cloud Keys
    (
        re.compile(r"\bAIza[0-9A-Za-z\-_]{30,}\b"),
        "[REDACTED:GOOGLE_API_KEY]",
    ),
    # AWS Access Key IDs
    (
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "[REDACTED:AWS_KEY_ID]",
    ),
    # Slack Tokens
    (
        re.compile(r"\bxox[baprs]-[0-9a-zA-Z]{10,}\b"),
        "[REDACTED:SLACK_TOKEN]",
    ),
    # Bearer tokens in headers or text
    (
        re.compile(r"(?i)\bBearer\s+[a-zA-Z0-9_\-\.]{20,}\b"),
        "Bearer [REDACTED:BEARER_TOKEN]",
    ),
    # Generic key-value secret assignments in config/.env/json/cli
    (
        re.compile(
            r"""(?i)\b(api[_-]?key|secret|password|passwd|auth[_-]?token|access[_-]?token)\s*[:=]\s*["']?([^\s"']{8,})["']?"""
        ),
        r"\1=[REDACTED:SECRET]",
    ),
]

SENSITIVE_FILE_NAMES: Set[str] = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.staging",
    ".env.development",
    "id_rsa",
    "id_ecdsa",
    "id_ed25519",
}

SENSITIVE_EXTENSIONS: Set[str] = {
    ".pem",
    ".key",
    ".pkcs12",
    ".pfx",
    ".p12",
}


def is_sensitive_path(path_str: str) -> bool:
    """Checks if a file path points to a known sensitive file."""
    try:
        p = Path(path_str.strip().strip("'\""))
        name_lower = p.name.lower()
        if name_lower in SENSITIVE_FILE_NAMES:
            return True
        if name_lower.startswith(".env") or name_lower.endswith(".env") or ".env." in name_lower:
            return True
        if p.suffix.lower() in SENSITIVE_EXTENSIONS:
            return True
        if "credential" in name_lower or "secret" in name_lower:
            if p.suffix.lower() in {".json", ".yaml", ".yml", ".toml", ".txt"}:
                return True
    except Exception:
        pass
    return False


def redact_text(text: str) -> Tuple[str, bool]:
    """Applies pattern-based redaction to text strings."""
    if not text or not isinstance(text, str):
        return text, False

    redacted = text
    modified = False

    for pattern, replacement in REDACTION_PATTERNS:
        new_text, count = pattern.subn(replacement, redacted)
        if count > 0:
            redacted = new_text
            modified = True

    return redacted, modified


def redact_payload(val: Any) -> Tuple[Any, bool]:
    """Recursively traverses dictionaries, lists, and strings to redact secrets and sensitive files."""
    if val is None:
        return None, False

    if isinstance(val, (int, float, bool)):
        return val, False

    if isinstance(val, str):
        return redact_text(val)

    if isinstance(val, dict):
        new_dict: Dict[str, Any] = {}
        was_redacted = False

        # Check if this dict represents a tool call reading a sensitive file
        path_arg = val.get("path") or val.get("Path") or val.get("file_path") or val.get("filepath")
        if isinstance(path_arg, str) and is_sensitive_path(path_arg):
            for k, v in val.items():
                if k in ("content", "Content", "output", "result", "replacement", "Replacement"):
                    new_dict[k] = "[REDACTED:SENSITIVE_FILE_CONTENT]"
                    was_redacted = True
                else:
                    new_v, r = redact_payload(v)
                    new_dict[k] = new_v
                    if r:
                        was_redacted = True
            return new_dict, was_redacted

        for k, v in val.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in ["password", "secret", "api_key", "token", "private_key"]) and isinstance(v, str):
                new_dict[k] = "[REDACTED:SECRET]"
                was_redacted = True
            else:
                new_v, r = redact_payload(v)
                new_dict[k] = new_v
                if r:
                    was_redacted = True
        return new_dict, was_redacted

    if isinstance(val, list):
        new_list = []
        was_redacted = False
        for item in val:
            new_item, r = redact_payload(item)
            new_list.append(new_item)
            if r:
                was_redacted = True
        return new_list, was_redacted

    return val, False
