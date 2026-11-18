# Memory Hook for Agents --- Engineering Improvement Plan

This document turns the repository review into an actionable
implementation plan. The goal is to improve reliability, security, and
maintainability without losing the project's main advantage:
lightweight, local, cross-provider memory with minimal runtime
dependencies.

## Goals

1.  Make memory writes and compaction reliable across crashes and
    concurrent hook executions.
2.  Make the capture behavior safer for sensitive data.
3.  Ensure the installed runner behaves exactly like the canonical
    package implementation.
4.  Make installation non-destructive to existing agent configuration.
5.  Improve recovery, testing, and the memory format before adding
    advanced retrieval features.

## Priority 0 --- Fix correctness and data-loss risks

### 1. Replace immediate compaction marking with an acknowledgement protocol

**Problem**

The runner currently emits a compaction/update notice and immediately
marks the current sequence as compacted. The agent may not actually
update `long.log`: it could ignore the notice, crash, or end the
session.

**Required changes**

-   Add explicit pending-compaction metadata to `state.json`, for
    example:
    -   `status`: `pending` or `acknowledged`
    -   `requested_at_seq`
    -   `requested_tokens`
    -   `acknowledged_at_seq`
    -   `requested_at` (timestamp)
-   When the threshold is reached:
    1.  Persist a pending compaction request.
    2.  Emit the instruction/notice to the agent.
    3.  Do **not** advance the acknowledged/compacted checkpoint yet.
-   Add an explicit acknowledgement operation that the agent or hook can
    invoke only after the long-term memory update has been written
    successfully.
-   On the next session start, detect a pending request and retry or
    surface it again.
-   Make acknowledgement idempotent so repeating it does not corrupt
    state.
-   Define what happens if `long.log` is missing, empty, or unchanged
    after a request. Do not silently acknowledge those cases.

**Acceptance criteria**

-   A notice alone never marks events as compacted.
-   A failed or interrupted compaction remains pending.
-   A successful acknowledgement records the checkpoint and clears the
    pending request.
-   Repeated acknowledgement is safe.
-   A new session can recover and retry an unfinished compaction.

**Tests to add/update**

-   Threshold reached → pending request created.
-   Notice emitted → checkpoint remains unchanged.
-   Acknowledgement → checkpoint advances.
-   Process exits before acknowledgement → request remains pending.
-   Pending request is retried on next session.
-   Duplicate acknowledgement → no duplicate state transition.
-   Update existing tests that currently expect compaction to be marked
    immediately after the notice.

### 2. Add cross-process locking around memory writes

**Problem**

Atomic replacement of `state.json` protects against a partially written
JSON file, but it does not prevent two hook processes from reading the
same old state and overwriting each other's updates.

**Required changes**

-   Add a cross-platform lock around the complete write transaction:
    1.  Acquire lock.
    2.  Read the latest state.
    3.  Allocate the next sequence number.
    4.  Append the event.
    5.  Apply pruning/retention metadata changes.
    6.  Persist state.
    7.  Release lock.
-   Keep the critical section small.
-   Ensure the lock is released if an exception occurs or the process
    exits.
-   Document lock timeout behavior. A timed-out writer should fail
    safely rather than write without a lock.
-   If practical, use a small, well-tested cross-platform locking
    dependency; otherwise isolate platform-specific locking behind one
    interface.

**Acceptance criteria**

-   Concurrent writers do not produce duplicate sequence numbers.
-   Token/event counters are not lost under concurrent writes.
-   A failed writer does not leave the system permanently locked.
-   Lock contention produces a clear diagnostic.

**Tests to add**

-   Run multiple writer processes simultaneously.
-   Assert unique, monotonically increasing sequence numbers.
-   Assert all expected events are present.
-   Simulate an exception while holding the lock and verify another
    process can proceed.

### 3. Make state recovery explicit

**Problem**

A malformed or partially corrupted `state.json` can cause the system to
fall back to defaults and lose sequence/counter continuity.

**Required changes**

-   Validate the state schema when loading it.
-   Distinguish between:
    -   state file does not exist (first run),
    -   state file is valid,
    -   state file exists but is malformed or incompatible.
-   For malformed state:
    -   preserve the damaged file as a timestamped backup,
    -   attempt recovery by scanning the event journal,
    -   reconstruct the highest valid sequence and any safely derivable
        counters,
    -   report fields that cannot be reconstructed.
-   Add a schema version to state and define migration behavior for
    future versions.
-   Never silently overwrite a malformed state file with an empty
    default.

**Acceptance criteria**

-   First-run initialization still works.
-   Corrupt state is preserved for inspection.
-   Recoverable sequence information is reconstructed from the journal.
-   Unrecoverable fields are reported rather than fabricated.

## Priority 1 --- Security and privacy

### 4. Add secret redaction before writing events to disk

**Problem**

The hook can capture prompts, tool arguments, tool outputs, and model
responses. Sensitive values can therefore enter `short.log`, including
API keys, access tokens, passwords, private keys, and contents of `.env`
files.

**Required changes**

-   Introduce a redaction layer that runs before any event is persisted.
-   Cover common patterns, including:
    -   API keys and bearer tokens,
    -   passwords and client secrets,
    -   private-key blocks,
    -   common cloud-provider credentials,
    -   `.env`-style assignments,
    -   authorization headers.
-   Make redaction configurable, but enable conservative defaults.
-   Add path-based exclusions for sensitive files and directories, such
    as `.env`, credential files, private keys, and local secret stores.
-   Add tool-based capture exclusions so users can disable capture for
    selected tools or event types.
-   Prefer replacing a secret with a marker such as
    `[REDACTED:API_KEY]`; do not preserve the original value in debug
    logs.
-   Document that pattern-based redaction is best-effort and cannot
    guarantee detection of every secret.

**Acceptance criteria**

-   Known test secrets never appear in persisted event payloads.
-   Redaction applies consistently to every captured field, not just
    user messages.
-   Excluded paths/tools are not persisted.
-   Redaction failures fail closed for configured sensitive fields.

**Tests to add**

-   API key, bearer token, password, private key, and `.env` fixtures.
-   Secrets embedded in nested dictionaries/lists.
-   Secrets in tool output and model response fields.
-   Excluded file path and excluded tool cases.
-   Verify the raw secret does not appear anywhere in generated log
    files.

### 5. Define a realistic security model

**Problem**

A generated `.pyc` runner and read-only file attributes do not make the
runner immutable or tamper-proof. A user-level process with the same
permissions can generally modify the files or their permissions.

**Required changes**

-   Describe the runner as trusted local installation infrastructure,
    not an immutable security boundary.
-   Treat read-only attributes/permissions as accidental-edit protection
    only.
-   Add an integrity check:
    -   record a hash for the installed runner,
    -   verify it before execution,
    -   report a clear error if it differs.
-   Keep installation/update operations separate from normal event
    capture.
-   Document the threat model:
    -   what the hook protects against,
    -   what it does not protect against,
    -   whether same-user malicious processes are in scope.
-   If protection against a hostile same-user process is a requirement,
    design for OS-level isolation and permissions; a Python hook alone
    cannot provide that guarantee.

**Acceptance criteria**

-   Documentation does not claim immutability or tamper-proofing.
-   Modified runner files are detected before execution.
-   The integrity check has tests for valid and modified files.
-   Limitations are stated clearly.

## Priority 2 --- Remove implementation drift and protect user configuration

### 6. Use one canonical runner implementation

**Problem**

The package runner and the standalone runner generated by the installer
contain separate implementations. Their event handling and normalization
can drift over time.

**Required changes**

Choose one of these approaches:

**Preferred:** generate the standalone artifact from the canonical
runner source, with only the packaging/bootstrap wrapper differing.

Alternative: move shared logic into a single source template/module and
ensure both execution modes use it.

-   Avoid maintaining two independently edited copies of normalization,
    event processing, and persistence logic.
-   Add a version identifier to the generated runner.
-   Make regeneration deterministic.

**Acceptance criteria**

-   There is one authoritative implementation of event processing.
-   Generated runner output is reproducible.
-   A change to normalization cannot silently affect only one runner
    variant.

**Tests to add**

-   Generate the standalone runner in a temporary directory.
-   Execute it in a subprocess with representative provider payloads.
-   Compare its normalized events and persisted state with the canonical
    runner.
-   Test installation and upgrade paths using the generated artifact.

### 7. Preserve existing agent hook configuration during installation

**Problem**

Replacing hook arrays or configuration sections can remove hooks that
the user already configured for other tools.

**Required changes**

-   Merge this project's hook entries into existing configuration rather
    than replacing the whole section.
-   Identify this project's entries using a stable unique identifier or
    exact command match.
-   Make installation idempotent: running it twice must not duplicate
    entries.
-   Preserve unrelated hooks, settings, comments where the config format
    permits, and unknown fields.
-   If a configuration file is malformed:
    -   do not silently replace it with `{}`,
    -   preserve a backup,
    -   stop and show a useful error or require explicit recovery.
-   Add a dry-run option that shows the changes before writing.
-   Create a backup before modifying user configuration.

**Acceptance criteria**

-   Existing unrelated hooks survive installation and upgrades.
-   Repeated installation does not create duplicates.
-   Malformed configuration is never silently overwritten.
-   The user can inspect the proposed changes with dry-run.

**Tests to add**

-   Existing config with multiple unrelated hooks.
-   Install twice.
-   Upgrade an older installed entry.
-   Malformed JSON/TOML/config fixture.
-   Verify backup creation and preservation of unknown fields.

## Priority 3 --- Improve maintainability and operational behavior

### 8. Make provider normalization explicit

**Problem**

Using generic normalization for providers with different event schemas
can lose provider-specific meaning or behave incorrectly as schemas
evolve.

**Required changes**

-   Keep the canonical internal event schema.
-   Add provider-specific adapters, for example:
    -   `ClaudeCodeAdapter`
    -   `CodexAdapter`
    -   `CursorAdapter`
    -   `AntigravityAdapter`
-   Keep provider parsing separate from persistence.
-   Add sanitized real-world fixture payloads for each supported
    provider.
-   Handle missing, optional, and unknown fields defensively.
-   Record the provider and source event type in the normalized event
    for debugging.

**Acceptance criteria**

-   Each provider has an independently testable adapter.
-   Unknown optional fields do not crash normalization.
-   Provider-specific behavior is covered by fixtures.
-   The canonical event schema remains provider-independent.

### 9. Improve log pruning and retention

**Problem**

Reading the entire journal into memory to prune it will become
increasingly expensive as the log grows.

**Required changes**

-   Implement streaming pruning or rotate the journal into bounded
    files.
-   Define configurable retention limits by size, age, or event count.
-   Ensure pruning never removes events that are still required by a
    pending compaction request.
-   Make retention behavior observable through status/diagnostic output.
-   Consider date-based journal rotation if it keeps the implementation
    simple.

**Acceptance criteria**

-   Pruning does not require loading the full journal into memory.
-   Pending events are retained until safely acknowledged.
-   Retention limits are documented and tested.

### 10. Label token counts as estimates

**Problem**

A `len(text) // 4`-style calculation is only a rough heuristic.
Token-to-character ratios vary by language, code, and model tokenizer.

**Required changes**

-   Name the field `estimated_tokens` or document the existing field as
    an estimate.
-   Make the estimation method explicit and configurable if needed.
-   Avoid presenting the estimate as an exact model token count.
-   If exact tokenization is ever added, make it optional so the
    zero-dependency installation remains available.

**Acceptance criteria**

-   CLI/status output and documentation consistently call these values
    estimates.
-   Threshold behavior is documented as approximate.

### 11. Add diagnostics and a status command

**Required changes**

Provide a concise diagnostic/status view that reports:

-   active memory directory,
-   installed providers and hook status,
-   runner version and integrity status,
-   short journal size and event count,
-   last sequence number,
-   long-memory file status and last modification time,
-   pending compaction status,
-   last successful write,
-   detected state/configuration warnings.

Do not print captured prompts, tool arguments, secrets, or full memory
contents by default.

**Acceptance criteria**

-   Users can diagnose a broken installation without opening internal
    files.
-   Sensitive content is not displayed by the status command.
-   Missing or inconsistent files produce actionable messages.

## Priority 4 --- Improve the memory model after reliability is established

### 12. Add structure and provenance to long-term memory

**Problem**

A single `long.log` Markdown file can become difficult to maintain as a
project accumulates decisions, architecture details, constraints, and
changing facts.

**Required changes**

Consider splitting durable memory into clearly scoped sections or files:

-   `project.md` --- purpose, current state, milestones.
-   `architecture.md` --- components, interfaces, and design.
-   `decisions.md` --- decisions, rationale, and dates.
-   `constraints.md` --- requirements, limitations, and non-goals.
-   `open-questions.md` --- unresolved questions and risks.

For each durable fact or decision, preserve useful provenance where
practical:

-   when it was recorded,
-   which event/session it came from,
-   whether it is confirmed, inferred, or superseded.

Define how conflicting facts are resolved. Newer information should not
automatically erase an older decision if the older decision explains why
the system is designed a certain way.

**Acceptance criteria**

-   Durable knowledge has clear categories.
-   Important decisions retain rationale and history.
-   Superseded facts can be distinguished from current facts.
-   Memory updates do not silently erase useful context.

### 13. Keep semantic retrieval optional

Do not add embeddings, a vector database, or an LLM dependency just to
make the project appear more sophisticated.

First improve deterministic retrieval:

-   project/repository namespaces,
-   recent-event retrieval,
-   category-based retrieval,
-   explicit decision lookup,
-   bounded context assembly.

Only add semantic search if real usage demonstrates that deterministic
retrieval is insufficient. Keep it optional and modular.

## Suggested implementation order

  -----------------------------------------------------------------------
  Order                   Work item               Why now
  ----------------------- ----------------------- -----------------------
  1                       Compaction              Prevents falsely
                          acknowledgement         marking unprocessed
                          protocol                events as compacted

  2                       Cross-process locking   Prevents concurrent
                                                  write races and lost
                                                  updates

  3                       State validation and    Protects continuity
                          recovery                after corruption or
                                                  interruption

  4                       Secret redaction and    Reduces accidental
                          capture exclusions      persistence of
                                                  sensitive data

  5                       Canonical runner        Prevents
                          generation              package/installed
                                                  behavior from diverging

  6                       Non-destructive config  Prevents installer from
                          merging                 damaging user setup

  7                       Provider-specific       Improves correctness
                          adapters and fixtures   across integrations

  8                       Streaming pruning and   Keeps storage bounded
                          retention               as usage grows

  9                       Diagnostics/status      Makes failures easier
                          command                 to investigate

  10                      Structured long-term    Improves
                          memory                  maintainability once
                                                  the foundation is
                                                  reliable

  11                      Optional retrieval      Add only when supported
                          enhancements            by actual usage
  -----------------------------------------------------------------------

## Suggested repository structure

This is a possible direction, not a requirement to adopt all these
modules immediately.

``` text
memory_hook/
    adapters/
        base.py
        claude_code.py
        codex.py
        cursor.py
        antigravity.py
    capture/
        policy.py
        redaction.py
    storage/
        journal.py
        locking.py
        state.py
        recovery.py
        retention.py
    compaction/
        manager.py
    runner.py
    installer.py
    diagnostics.py
    cli.py

tests/
    fixtures/
        claude_code/
        codex/
        cursor/
        antigravity/
    test_compaction.py
    test_concurrency.py
    test_recovery.py
    test_redaction.py
    test_installer_merge.py
    test_generated_runner.py
```

Do not reorganize the whole project in one pass. Extract modules as each
feature is implemented and tested.

## Definition of done

Before calling the next release reliable, verify all of the following:

-   [ ] A compaction request remains pending until explicitly
    acknowledged.
-   [ ] Interrupted compaction is retried or surfaced on the next
    session.
-   [ ] Concurrent hook processes cannot lose events or duplicate
    sequence numbers.
-   [ ] Corrupt state is backed up and recovered where possible.
-   [ ] Secrets are redacted before persistence, with configurable
    exclusions.
-   [ ] Documentation accurately describes the security boundary and
    limitations.
-   [ ] The installed runner is generated from the canonical
    implementation.
-   [ ] Installer changes preserve existing hooks and back up
    configuration.
-   [ ] Each supported provider has fixture-based adapter tests.
-   [ ] Pruning is bounded and does not delete unacknowledged events.
-   [ ] Token counts are clearly identified as estimates.
-   [ ] Diagnostics reveal installation and state problems without
    exposing captured content.

## Scope discipline

The project does not need a vector database, a hosted service, or a
complex agent framework to solve its core problem. Prioritize
correctness, recoverability, privacy, and predictable integration
behavior. Those qualities will make the existing lightweight
architecture substantially more useful than adding features before the
persistence model is dependable.
