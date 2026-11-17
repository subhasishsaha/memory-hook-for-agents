"""Normalizes heterogeneous provider payloads into standard canonical events."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .constants import (
    EVENT_MODEL_RESPONSE,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_USER_REQUEST,
    MAX_PAYLOAD_STRING_LENGTH,
    PROVIDER_ANTIGRAVITY,
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    PROVIDER_CURSOR,
    PROVIDER_GENERIC,
)


def extract_model_response(raw_payload: Dict[str, Any]) -> str:
    """Extracts model response text from direct fields or agent transcript files."""
    # 1. Direct fields
    for k in [
        "response",
        "last_assistant_message",
        "last_response",
        "assistant_message",
        "text",
        "output",
    ]:
        val = raw_payload.get(k)
        if isinstance(val, str) and val.strip():
            return val
        elif isinstance(val, dict) and val.get("content"):
            return str(val["content"])

    # 2. Antigravity / Agent transcript resolution
    transcript_paths = []
    tp = raw_payload.get("transcriptPath")
    if tp:
        p = Path(tp)
        transcript_paths.append(p.parent / "transcript_full.jsonl")
        transcript_paths.append(p)

    cid = raw_payload.get("conversationId") or raw_payload.get("sessionId")
    if cid:
        home = Path.home()
        for variant in ["antigravity-cli", "antigravity", "antigravity-ide"]:
            log_dir = home / ".gemini" / variant / "brain" / cid / ".system_generated" / "logs"
            transcript_paths.append(log_dir / "transcript_full.jsonl")
            transcript_paths.append(log_dir / "transcript.jsonl")

    for tp_cand in transcript_paths:
        if tp_cand.is_file():
            try:
                with open(tp_cand, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                    for line in reversed(lines[-100:]):
                        item = json.loads(line)
                        if (
                            item.get("type") == "PLANNER_RESPONSE"
                            or item.get("source") == "MODEL"
                        ) and item.get("content"):
                            return str(item["content"])
            except Exception:
                continue

    return ""


def truncate_string(val: Any, max_len: int = MAX_PAYLOAD_STRING_LENGTH) -> Tuple[Any, bool]:
    """Truncates string values if they exceed maximum length, flagging truncation."""
    if isinstance(val, str):
        if len(val) > max_len:
            return val[:max_len] + f"\n... [truncated {len(val) - max_len} characters]", True
        return val, False
    elif isinstance(val, dict):
        truncated_any = False
        new_dict = {}
        for k, v in val.items():
            new_v, was_trunc = truncate_string(v, max_len)
            new_dict[k] = new_v
            if was_trunc:
                truncated_any = True
        return new_dict, truncated_any
    elif isinstance(val, list):
        truncated_any = False
        new_list = []
        for item in val:
            new_item, was_trunc = truncate_string(item, max_len)
            new_list.append(new_item)
            if was_trunc:
                truncated_any = True
        return new_list, truncated_any
    return val, False


def detect_provider(raw_payload: Dict[str, Any], hint: Optional[str] = None) -> str:
    """Infers the agent provider from CLI hint or payload characteristics."""
    if hint:
        h = hint.lower().strip()
        if h in [PROVIDER_CLAUDE, "claude"]:
            return PROVIDER_CLAUDE
        if h in [PROVIDER_ANTIGRAVITY, "gemini"]:
            return PROVIDER_ANTIGRAVITY
        if h in [PROVIDER_CODEX, "openai"]:
            return PROVIDER_CODEX
        if h in [PROVIDER_CURSOR, "cursor"]:
            return PROVIDER_CURSOR

    if "conversationId" in raw_payload or "artifactDirectoryPath" in raw_payload:
        return PROVIDER_ANTIGRAVITY
    if "sessionId" in raw_payload and ("prompt" in raw_payload or "tool_input" in raw_payload):
        return PROVIDER_CLAUDE
    if "cursor" in str(raw_payload).lower():
        return PROVIDER_CURSOR
    if "codex" in str(raw_payload).lower():
        return PROVIDER_CODEX

    return PROVIDER_GENERIC


def normalize_event(
    raw_payload: Dict[str, Any],
    event_hint: Optional[str] = None,
    provider_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Normalizes any incoming provider hook payload into a canonical event object."""
    provider = detect_provider(raw_payload, provider_hint)
    event_name = (
        event_hint or raw_payload.get("hookEventName") or raw_payload.get("event") or ""
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    session_id = (
        raw_payload.get("conversationId")
        or raw_payload.get("sessionId")
        or raw_payload.get("session_id")
        or "unknown_session"
    )
    turn_id = (
        raw_payload.get("invocationNum")
        or raw_payload.get("stepIdx")
        or raw_payload.get("turn_id")
        or None
    )

    event_type = EVENT_USER_REQUEST
    payload: Dict[str, Any] = {}
    is_truncated = False

    # 1. Antigravity normalization
    if provider == PROVIDER_ANTIGRAVITY:
        if event_name in ["PreInvocation", "SessionStart"]:
            event_type = EVENT_USER_REQUEST
            text = raw_payload.get("lastUserInput") or ""
            text_trunc, was_trunc = truncate_string(text)
            payload = {"content": text_trunc}
            is_truncated = was_trunc

        elif event_name == "PreToolUse":
            event_type = EVENT_TOOL_CALL
            tc = raw_payload.get("toolCall", {})
            call_id = tc.get("id") or f"call_{turn_id}"
            tool_name = tc.get("name") or "unknown_tool"
            args, was_trunc = truncate_string(tc.get("args", {}))
            payload = {"call_id": call_id, "tool_name": tool_name, "args": args}
            is_truncated = was_trunc

        elif event_name == "PostToolUse":
            event_type = EVENT_TOOL_RESULT
            tc = raw_payload.get("toolCall", {})
            call_id = tc.get("id") or f"call_{turn_id}"
            tool_name = tc.get("name") or "unknown_tool"
            error = raw_payload.get("error")
            content = raw_payload.get("result") or raw_payload.get("output") or ""
            if not content and not error:
                content = "Tool executed successfully."
            content_trunc, was_trunc = truncate_string(content)
            payload = {
                "call_id": call_id,
                "tool_name": tool_name,
                "status": "error" if error else "success",
                "content": content_trunc,
                "error": error,
            }
            is_truncated = was_trunc

        elif event_name in ["Stop", "PostInvocation"]:
            event_type = EVENT_MODEL_RESPONSE
            response_text = extract_model_response(raw_payload)
            resp_trunc, was_trunc = truncate_string(response_text)
            payload = {"content": resp_trunc}
            is_truncated = was_trunc

    # 2. Claude Code normalization
    elif provider == PROVIDER_CLAUDE:
        if event_name in ["UserPromptSubmit", "prompt"]:
            event_type = EVENT_USER_REQUEST
            text = raw_payload.get("prompt") or raw_payload.get("user_prompt") or ""
            text_trunc, was_trunc = truncate_string(text)
            payload = {"content": text_trunc}
            is_truncated = was_trunc

        elif event_name == "PreToolUse":
            event_type = EVENT_TOOL_CALL
            call_id = raw_payload.get("tool_use_id") or raw_payload.get("id") or f"call_{turn_id}"
            tool_name = raw_payload.get("tool_name") or raw_payload.get("tool") or "unknown_tool"
            args, was_trunc = truncate_string(raw_payload.get("tool_input") or raw_payload.get("args") or {})
            payload = {"call_id": call_id, "tool_name": tool_name, "args": args}
            is_truncated = was_trunc

        elif event_name == "PostToolUse":
            event_type = EVENT_TOOL_RESULT
            call_id = raw_payload.get("tool_use_id") or raw_payload.get("id") or f"call_{turn_id}"
            tool_name = raw_payload.get("tool_name") or raw_payload.get("tool") or "unknown_tool"
            error = raw_payload.get("error")
            content = raw_payload.get("tool_response") or raw_payload.get("response") or ""
            content_trunc, was_trunc = truncate_string(content)
            payload = {
                "call_id": call_id,
                "tool_name": tool_name,
                "status": "error" if error else "success",
                "content": content_trunc,
                "error": error,
            }
            is_truncated = was_trunc

        elif event_name in ["Stop", "SessionEnd"]:
            event_type = EVENT_MODEL_RESPONSE
            resp_text = extract_model_response(raw_payload)
            resp_trunc, was_trunc = truncate_string(resp_text)
            payload = {"content": resp_trunc}
            is_truncated = was_trunc

    # 3. Generic / Codex / Cursor normalization
    else:
        lower_event = event_name.lower()
        if "prompt" in lower_event or "submit" in lower_event or "user" in lower_event:
            event_type = EVENT_USER_REQUEST
            text = raw_payload.get("prompt") or raw_payload.get("content") or raw_payload.get("text") or ""
            text_trunc, was_trunc = truncate_string(text)
            payload = {"content": text_trunc}
            is_truncated = was_trunc

        elif "pretool" in lower_event or "beforetool" in lower_event or "shell" in lower_event:
            event_type = EVENT_TOOL_CALL
            call_id = raw_payload.get("id") or raw_payload.get("call_id") or f"call_{turn_id}"
            tool_name = raw_payload.get("name") or raw_payload.get("tool") or raw_payload.get("tool_name") or "tool"
            args, was_trunc = truncate_string(raw_payload.get("args") or raw_payload.get("parameters") or raw_payload.get("input") or {})
            payload = {"call_id": call_id, "tool_name": tool_name, "args": args}
            is_truncated = was_trunc

        elif "posttool" in lower_event or "aftertool" in lower_event:
            event_type = EVENT_TOOL_RESULT
            call_id = raw_payload.get("id") or raw_payload.get("call_id") or f"call_{turn_id}"
            tool_name = raw_payload.get("name") or raw_payload.get("tool") or raw_payload.get("tool_name") or "tool"
            content, was_trunc = truncate_string(raw_payload.get("output") or raw_payload.get("result") or "")
            error = raw_payload.get("error")
            payload = {
                "call_id": call_id,
                "tool_name": tool_name,
                "status": "error" if error else "success",
                "content": content,
                "error": error,
            }
            is_truncated = was_trunc

        elif "response" in lower_event or "stop" in lower_event:
            event_type = EVENT_MODEL_RESPONSE
            resp_text = extract_model_response(raw_payload)
            resp_trunc, was_trunc = truncate_string(resp_text)
            payload = {"content": resp_trunc}
            is_truncated = was_trunc
        else:
            # Fallback
            event_type = EVENT_USER_REQUEST
            text_trunc, was_trunc = truncate_string(raw_payload)
            payload = {"raw": text_trunc}
            is_truncated = was_trunc

    return {
        "ts": now_iso,
        "provider": provider,
        "session_id": session_id,
        "turn_id": turn_id,
        "type": event_type,
        "payload": payload,
        "truncated": is_truncated,
    }
