# AI Session Continuity & Work Packet Standard

## Purpose

Long-running AI-assisted engineering must not depend on replaying or copying old ChatGPT/Cursor conversations.

Conversation history is temporary working context. Durable engineering state belongs in Git, GitHub, tests, specifications, and a small repository-scoped working-state record.

The default working-state record is an **AI Work Packet** stored as a GitHub Issue in the same repository as the work it describes.

## Why repository-scoped

Do not use one global cross-project handoff document.

The repository is the namespace boundary:

```text
project repository
  -> canonical code/spec/tests
  -> repository-scoped AI Work Packet Issues
```

This prevents an agent working on one project from accidentally loading another project's state.

Do not create a separate workspace repository merely to hold handoffs unless a future requirement cannot be satisfied by repository-scoped Issues.

## Why Issue state, not a committed HANDOFF file

Release-candidate repositories may require exact-HEAD qualification.

Updating a committed handoff file changes the product Git SHA and can invalidate exact-HEAD evidence.

Updating a GitHub Issue does not change the repository source HEAD.

Therefore live session state must not be committed to the qualified product branch solely for AI continuity.

## Unit of continuity

Use one Work Packet per active **workstream**, not one packet per repository.

Examples:

```text
[AI Work] v2.4.0 Release Closure
[AI Work] Website Refresh
[AI Work] MCP Design
```

Multiple workstreams may coexist safely when packet selection is deterministic.

## Required identity fields

Every packet body begins with:

```text
PACKET_VERSION=1
TARGET_REPO=owner/repository
WORKSTREAM=<stable-slug>
STATUS=ACTIVE|PAUSED|BLOCKED|COMPLETE
BRANCH=<branch-name|N/A>
LAST_VERIFIED_HEAD=<40-char-sha|UNKNOWN>
```

`LAST_VERIFIED_HEAD` is evidence of the last observed state, not authority. The current repository state must be re-verified on resume.

## Required sections

Keep the packet short and current:

```text
Goal
Current State
Next Action
Constraints
Canonical References
Latest Evidence
Blockers
```

### Goal

One concise statement of the workstream outcome.

### Current State

Only facts needed to resume now.

### Next Action

Exactly the next bounded action or phase. Do not embed an entire historical prompt chain.

### Constraints

Current scope/safety restrictions that materially affect execution.

### Canonical References

Paths or links to authoritative repository artifacts. Reference them; do not copy their contents into the packet.

### Latest Evidence

Compact executed evidence such as exact HEAD, targeted test result, CI run/PR link, or explicit NOT_RUN/BLOCKED state.

### Blockers

Only current blockers.

## What must not be copied into a Work Packet

Do not paste:

- old conversations
- previous handoff prompts
- previous Cursor prompts
- Product Master/specification contents
- Engineering System contents
- raw multi-megabyte logs
- complete historical phase reports
- secrets, credentials, private keys, tokens
- user-specific absolute local paths unless strictly necessary and non-sensitive

Link to canonical files, commits, PRs, CI runs, Issues, or retained evidence instead.

## Current-state overwrite rule

A Work Packet is a current-state record, not an append-only diary.

After meaningful progress:

- replace `Current State`
- replace `Next Action`
- replace `Latest Evidence`
- replace `Blockers`
- update `LAST_VERIFIED_HEAD`

Do not keep accumulating old phase text in the Issue body.

History already exists in Git commits, PRs, CI, Issue edits/comments, and closed Issues.

## Deterministic packet resolution

When resuming work:

1. Resolve the target repository first from the current Git remote or explicit user/project context.
2. Once resolved, do not search unrelated repositories.
3. Resolve the current branch when a local repository is available.
4. Read only open Issues marked as AI Work Packets (default label: `ai-work`).
5. Require exact `TARGET_REPO` match.
6. Prefer an exact `BRANCH` match when branch context exists.
7. Require exactly one matching `STATUS=ACTIVE` packet.
8. Zero matches: report no active packet; do not reconstruct state from guesses.
9. Multiple matches: fail closed and ask which workstream to use.
10. Verify actual repository branch, HEAD, dirty state, PR/CI state, and relevant canonical files before acting.

Never treat a stale packet HEAD as current truth.

## Resume context budget

After selecting the packet:

1. Read repository `AGENTS.md`.
2. Read `.engineering/project.yaml`.
3. Read test/release metadata only when relevant.
4. Read only canonical references required by `Next Action`.
5. Do not preload all references named in the packet.

The Work Packet replaces a large handoff; it must not become another large handoff.

## ChatGPT behavior

When the user asks to continue/resume an existing engineering workstream:

- resolve the target repository
- load its active Work Packet
- verify current GitHub/repository facts
- continue from `Next Action`
- do not ask the user to paste prior chat unless the required durable state genuinely does not exist

## Cursor behavior

Repository adoption should provide `.cursor/commands/resume.md`.

The resume command:

- derives repository/branch/HEAD from Git
- loads the repository-scoped active Work Packet through an available GitHub integration or authenticated `gh`
- fails closed on missing/ambiguous packets
- reads only task-relevant canonical references
- executes the current `Next Action`
- updates the same packet with concise verified state/evidence at completion

The default remains single-agent sequential execution when the owner's project rules require it.

## GitHub label and lifecycle

Default label:

```text
ai-work
```

Create the label once per adopted repository.

Use:

```text
ACTIVE   -> open Issue
PAUSED   -> open Issue
BLOCKED  -> open Issue
COMPLETE -> close Issue
```

A new independent workstream gets a new Issue rather than reusing an unrelated completed packet.

## Evidence and authority

Authority remains:

1. actual code/config/runtime and immutable Git state
2. canonical product/specification artifacts
3. repository engineering metadata
4. executed deterministic evidence
5. AI Work Packet as current coordination state
6. conversation history

The Work Packet never overrides code, tests, canonical specifications, Git state, or release evidence.

## Adoption requirements

For repositories using session continuity:

- add the `ai-work` label
- add the AI Work Packet Issue template
- add Cursor `/resume` command when Cursor is used
- ensure ChatGPT/Cursor adapters know to resolve repository first
- never store secrets in Work Packets
- never use a Work Packet update as release evidence
