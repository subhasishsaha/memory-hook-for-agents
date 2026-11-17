"""Templates and generators for provider-specific agent instruction files."""

LONG_LOG_TEMPLATE = """# Project Memory & Knowledge Base
> Last Updated: {timestamp} | Provider: {provider} | Synced Through Event: #{seq}

## 1. Project Overview & Primary Goals
- **Mission**: {mission}
- **Target Audience / Use Case**: {use_case}

## 2. Technology Stack & Environment
- **Languages & Runtimes**: {stack}
- **Key Frameworks & Libraries**: {frameworks}
- **Repository Structure**: Brief map of core directories.

## 3. Architecture & Core Decisions
- [x] Initialized cross-provider memory hook tracking.

## 4. Completed Work & Milestones
- [x] Repository configured with deterministic event logging.

## 5. Current State & Active Focus
- **Current Task**: Initial project setup and memory initialization.
- **Active Context**: Listening to model and tool lifecycle events.

## 6. Next Immediate Steps
1. Execute project requirements.
2. Update `.memory/long.log` when prompted.

## 7. Known Constraints & Gotchas
- All actions are logged deterministically to `.memory/short.log`.
"""

AGENT_MEMORY_INSTRUCTIONS = """# Universal Memory Hook Protocol (.memory/)

This repository is integrated with the **Cross-Provider Memory Hook**.
All agent activities (user requests, tool calls, tool responses, model outputs) are deterministically recorded in `.memory/short.log`.

## Core Directives for All Agents:

1. **Context Restoration at Session Start**:
   - At the beginning of every session or task, read `.memory/long.log` first.
   - If needed, read the last 15 lines of `.memory/short.log` to catch up on the most recent actions taken by any previous agent (Claude, Antigravity, Codex, Cursor).
   - Never ask the user to re-explain what the project is about or what has already been built.

2. **Long-Term Memory Compaction**:
   - Whenever you receive a `[MEMORY HOOK UPDATE NOTICE]` indicating 10 new events have been logged, review the uncompacted tail of `.memory/short.log` alongside the existing `.memory/long.log`.
   - Use your model tools (`edit_file` or `write_file`) to update `.memory/long.log`:
     - Update the header metadata: `Last Updated`, `Provider`, and `Synced Through Event: #<seq>`.
     - Update **Completed Work & Milestones** with newly finished items.
     - Update **Current State & Active Focus** with what you are working on right now.
     - Update **Next Immediate Steps** with pending tasks.
     - Preserve and append any newly established **Architecture & Core Decisions**.
     - Keep content concise, high-density, and structured.
"""


def get_agents_md_content() -> str:
    """Returns content for Antigravity AGENTS.md."""
    return f"""# Antigravity Agent Guidelines

{AGENT_MEMORY_INSTRUCTIONS}
"""


def get_claude_md_content() -> str:
    """Returns content for Claude Code CLAUDE.md."""
    return f"""# Claude Code Guidelines

{AGENT_MEMORY_INSTRUCTIONS}
"""


def get_codex_md_content() -> str:
    """Returns content for OpenAI Codex CODEX.md."""
    return f"""# Codex Guidelines

{AGENT_MEMORY_INSTRUCTIONS}
"""


def get_cursorrules_content() -> str:
    """Returns content for Cursor .cursorrules."""
    return f"""# Cursor Rules

{AGENT_MEMORY_INSTRUCTIONS}
"""
