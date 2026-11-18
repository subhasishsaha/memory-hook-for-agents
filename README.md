# memory-hook

[![Python Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Supported Providers](https://img.shields.io/badge/providers-Claude%20%7C%20Antigravity%20%7C%20Codex%20%7C%20Cursor-blue.svg)]()

> A universal, downloadable lifecycle hook that bridges context across AI coding agents (**Claude Code**, **Antigravity**, **OpenAI Codex**, and **Cursor**).

Never re-explain your project again when switching between different agentic CLI tools or IDE assistants.

---

## The Problem It Solves

Developers frequently switch between different AI agents:
- You start a complex architectural refactor in **Antigravity**.
- You move to **Claude Code** for fast terminal editing and bash tasks.
- You open **Cursor** or **OpenAI Codex** for code completions or specific model evaluations.

**The pain:** Every time you switch, the new agent starts with zero knowledge of what the previous agent planned, tested, discovered, or decided.

**The solution:** `memory-hook` installs lightweight deterministic lifecycle hooks into your repository. It intercepts model responses, user prompts, tool requests, and tool results, preserving context in a structured `.memory/` directory that any agent immediately understands.

---

## Key Features

- **Cross-Provider Compatibility**: Native support for **Antigravity** (`.agents/hooks.json`), **Claude Code** (`.claude/settings.json`), **OpenAI Codex** (`.codex/hooks.json`), and **Cursor** (`.cursor/hooks.json`).
- **Deterministic Event Stream (`.memory/short.log`)**: Records every action, tool parameter, tool result, and model response in append-only JSON Lines format.
- **100k Token Rolling Context Window**: Automatically prunes older entries from `.memory/short.log` to preserve a rolling 100,000 token context window, preventing file bloat and runaway context.
- **Agentic Knowledge Synthesis (`.memory/long.log`)**: Distills project identity, architecture, active tasks, and decisions into high-density Markdown.
- **Token-Based Compaction Trigger (10k Tokens)**: Whenever $\ge 10{,}000$ tokens accumulate since the last synthesis, the hook injects a prompt directive instructing the active agent to distill recent progress into `long.log`.
- **Local Bytecode Compilation & OS Read-Only Lock**: Ships clean Python source code, compiles `.pyc` locally on the user's system at install time, deletes `.py` source, and locks `.memory/bin/memory_hook_runner.pyc` with OS-level read-only permissions (`attrib +R` / `chmod 0o555`) to prevent agents from tampering with runner scripts.
- **Non-Destructive Instruction Appending**: Preserves existing `AGENTS.md`, `CLAUDE.md`, `CODEX.md`, and `.cursorrules` files by appending memory protocols cleanly at the bottom without overwriting existing guidelines.
- **Zero Runtime Dependencies**: Works entirely with Python standard library. No external pip dependencies required at runtime.

---

## Directory Structure

When installed into a project, `memory-hook` creates:

```
<your-project>/
├── .memory/
│   ├── short.log                 # Rolling context event log (JSONL, capped at 100k tokens)
│   ├── long.log                  # Distilled, high-density project memory (Markdown)
│   ├── state.json                # Sequence counters, token accounting, and compaction checkpoints
│   └── bin/
│       └── memory_hook_runner.pyc # Locally compiled bytecode runner (OS read-only locked)
├── .agents/                      # Antigravity hook bindings
│   └── hooks.json
├── AGENTS.md                     # Antigravity context restoration instructions
├── .claude/                      # Claude Code hook bindings
│   └── settings.json
├── CLAUDE.md                     # Claude Code context restoration instructions
├── .codex/                       # OpenAI Codex hook bindings
│   └── hooks.json
├── CODEX.md                      # Codex instructions
├── .cursor/                      # Cursor hook bindings
│   └── hooks.json
└── .cursorrules                  # Cursor instructions
```

---

## Quick Start & Installation

### Option 1: One-Line Standalone Installer (Recommended)

#### Linux / macOS:
```bash
curl -fsSL https://raw.githubusercontent.com/subhasishsaha/memory-hook-for-agents/main/install.sh | bash
```

#### Windows (PowerShell):
```powershell
irm https://raw.githubusercontent.com/subhasishsaha/memory-hook-for-agents/main/install.ps1 | iex
```

Or run directly with Python:
```bash
python install.py /path/to/project
```

---

### Option 2: Python / Pip CLI

```bash
# Install package from GitHub
pip install git+https://github.com/subhasishsaha/memory-hook-for-agents.git

# Install into current project
memory-hook install

# Or install for a specific provider only
memory-hook install --provider antigravity
memory-hook install --provider claude
```

---

### Option 3: NPX / Node.js

```bash
npx memory-hook install
```

---

## How It Works

```mermaid
flowchart TD
    User([User Prompt]) --> Agent[AI Agent: Claude / Antigravity / Codex / Cursor]
    Agent --> PreTool[PreToolUse / PreInvocation Hook]
    PreTool --> Runner[memory_hook_runner.pyc]
    Runner --> ShortLog[(.memory/short.log)]
    Runner --> Prune[Rolling Window Check: 100k tokens]
    Prune --> State[(.memory/state.json)]
    State --> Check{Uncompacted >= 10k tokens?}
    Check -- No --> Continue[Continue Agent Execution]
    Check -- Yes --> Inject[Inject Prompt Directive: Update long.log]
    Inject --> Agent
    Agent --> WriteLong[Model synthesizes .memory/long.log]
    Agent --> PostTool[PostToolUse / Stop Hook]
    PostTool --> Runner
```

### Event Normalization Schema (`short.log`)

Every intercepted event is appended as a single JSON object:

```json
{
  "seq": 14,
  "ts": "2026-11-18T00:15:30.120Z",
  "provider": "antigravity",
  "session_id": "ec33ebf9-0cba-4100-8142-c61503f6c587",
  "turn_id": 12,
  "type": "tool_call",
  "payload": {
    "call_id": "call_123",
    "tool_name": "run_command",
    "args": { "CommandLine": "npm test" }
  },
  "tokens": 28,
  "truncated": false
}
```

### Event Types:
- `user_request`: Prompts and instructions submitted by the user.
- `tool_call`: Tool invocation parameters emitted by the agent.
- `tool_result`: Output or error returned by the executed tool.
- `model_response`: Natural language response emitted by the model.

---

## Security & Immutability

Because AI coding agents have file system access, `memory-hook` enforces a multi-layered security model to protect hook infrastructure from prompt injection or accidental model modification:

1. **Local Bytecode Compilation (`.pyc`)**: The runner source code is compiled into bytecode using the user's active Python interpreter during installation. The plain `.py` source is immediately purged. LLM text-editing tools (`edit_file`, `patch`) cannot edit binary bytecode.
2. **OS-Level Read-Only Attribute**: `.memory/bin/memory_hook_runner.pyc` is set to read-only (`attrib +R` on Windows, `chmod 0o555` on Unix). File overwrite and deletion attempts raise an OS `PermissionError`.
3. **Agent Protocol Directives**: `AGENTS.md`, `CLAUDE.md`, `CODEX.md`, and `.cursorrules` explicitly forbid agents from touching `.memory/bin/` or `.memory/state.json`, directing all persistent memory updates strictly to `.memory/long.log`.

---

## CLI Commands

```bash
# Check memory hook status, event counters, and compaction health
memory-hook status

# Inspect recent short.log events
memory-hook inspect --tail 10

# Print current long-term memory summary
memory-hook inspect --long

# Reinstall or reconfigure providers
memory-hook install --provider all
```

---

## Running Tests

The test suite runs with standard Python `unittest`:

```bash
python -m unittest discover tests
```

---

## License

MIT © [Memory Hook Contributors](LICENSE)
