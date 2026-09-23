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
PACKET_VERSION=2
TARGET_REPO=owner/repository
WORKSTREAM=<stable-slug>
STATUS=ACTIVE|PAUSED|BLOCKED|COMPLETE
BRANCH=<branch-name|N/A>
TASK_KIND=DESIGN|DEVELOPMENT|TEST|REVIEW|RELEASE|OPERATIONS|ADOPTION|DOCUMENTATION|CLEANUP|MIXED
OWNER_INTENT=<one concise line describing the owner's current explicit request>
LAST_VERIFIED_HEAD=<40-char-sha|UNKNOWN>
```

`LAST_VERIFIED_HEAD` is evidence of the last observed state, not authority. The current repository state must be re-verified on resume.

`TASK_KIND` and `OWNER_INTENT` describe the current bounded handoff, not the lifetime purpose of the workstream. Refresh them whenever the owner's explicit request changes materially.

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

## Owner-intent synchronization

Before handing work to an implementation agent, the coordinating agent must synchronize the packet with the owner's latest explicit request:

1. Set `TASK_KIND` to the current execution phase.
2. Set `OWNER_INTENT` to one concise statement of what the owner is asking for now.
3. Ensure `Next Action` directly advances both the workstream `Goal` and `OWNER_INTENT`.
4. Do not silently substitute an older release, cleanup, or validation step merely because it was previously pending.
5. If the new request is still the same workstream, update the existing packet. If it is a genuinely independent workstream, create a separate packet.
6. If `Goal`, `OWNER_INTENT`, `TASK_KIND`, and `Next Action` materially conflict, do not execute the packet. Report `WORK_PACKET_SCOPE_MISMATCH` and obtain or record the minimum correction needed.

`STATUS` is deliberately small and fixed. Do not invent transient values such as `CURSOR_READY`, `WAITING`, or `DONE`.

- `ACTIVE` — work is runnable or waiting on a machine-observable condition that can be resumed automatically.
- `PAUSED` — the owner intentionally paused the workstream.
- `BLOCKED` — progress requires a human/external action or a required execution environment is unavailable.
- `COMPLETE` — terminal; no executable `Next Action` remains.

"Ready for Cursor" is represented by `STATUS=ACTIVE` plus a valid `Next Action`, not by a new status value.

Packet version 1 is legacy-compatible. Agents may resume a valid v1 packet, but should migrate it to v2 fields on the next meaningful packet update rather than blocking solely because `TASK_KIND` or `OWNER_INTENT` is absent.

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

- refresh `TASK_KIND` and `OWNER_INTENT` when the owner's request changes
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
4. Read only open Issues whose title begins with `[AI Work]`. An `ai-work` label may be used as an optional search accelerator, but must not be required for correctness.
5. Require exact `TARGET_REPO` match.
6. Prefer an exact `BRANCH` match when branch context exists.
7. Require exactly one matching `STATUS=ACTIVE` packet. Reject non-canonical status values rather than treating them as aliases.
8. For packet v2, require `TASK_KIND` and `OWNER_INTENT`, and verify that `Next Action` directly advances the packet `Goal` and current owner intent. If they materially disagree, stop with `WORK_PACKET_SCOPE_MISMATCH`; do not repair the mismatch by searching unrelated chats, Athena, or other repositories.
9. For legacy packet v1, use `Goal` + `Next Action` conservatively and migrate the packet to v2 on the next meaningful update.
10. Zero matches: report no active packet; do not reconstruct state from guesses.
11. Multiple matches: fail closed and ask which workstream to use.
12. Verify actual repository branch, HEAD, dirty state, PR/CI state, and relevant canonical files before acting.

Never treat a stale packet HEAD as current truth.

## Resume context budget

After resolving Git identity and before ordinary work:

1. Check whether repository `AGENTS.md` and `.engineering/project.yaml` exist.
2. If they exist, read them first.
3. If the repository shows Engineering System adoption markers (for example `.engineering/`, `.cursor/rules/engineering-system.mdc`, managed `engineering-system.yml`, or session-continuity adapters) but mandatory `AGENTS.md` or `.engineering/project.yaml` is missing or unreadable, record `ENGINEERING_SYSTEM_ADOPTION=INCOMPLETE` and fail closed unless the selected packet is an explicit adoption-repair flow (`TASK_KIND=ADOPTION`).
4. If they are absent because the repository has not yet adopted the Engineering System or adoption is intentionally pending in a separate workstream/PR, record `ENGINEERING_SYSTEM_ADOPTION=ABSENT_OR_PENDING` and continue under the canonical Engineering System default. Do not create, merge, or modify adoption files unless the current Work Packet explicitly authorizes that work.
5. Read test/release metadata only when relevant and only if present for the adopted project state.
6. Read only canonical references required by `Next Action`.
7. Use minimum sufficient reasoning/context; do not request maximum reasoning by default.
8. Do not preload all references named in the packet.
9. Do not keep a coding-agent session alive polling CI, review, deployment, or another machine-observable external condition. Record a concise `WAITING_FOR_<CONDITION>` state and yield to coordinator/automation; the next resume re-checks the condition.
10. For an existing branch/PR, orient from the base diff first (`git diff --name-only`/`git diff --stat`) before broad repository search.
11. Bound tool output: retain verbose logs outside model context and surface exit status plus focused grep/tail evidence; expand only on failure or ambiguity.
12. Prefer a fresh coding-agent session for each new bounded `Next Action` only after `tools/cursor-resource-preflight.py` returns exit 0 (`PASS` or `WARN`). On `BLOCK`, do not create a new persistent session and do not stop, kill, or mutate existing sessions. Resource safety takes precedence over a fresh session. Reuse an already-running matching target session when that reuse is safe and semantically correct. Persistent sessions are for an in-flight action/process, not long-term memory.

Never-adopted repositories may continue under the canonical default. Incomplete adopted repositories must not silently continue ordinary work without mandatory project context.

The Work Packet replaces a large handoff; it must not become another large handoff.

## Trusted Work Packet provenance

An executable AI Work Packet must come from authenticated repository-scoped GitHub state for the resolved `TARGET_REPO`.

Trusted sources:

- authenticated `gh` against the exact origin repository
- an available authenticated GitHub integration bound to that same repository

Trusted Work Packet Issue authors are limited by effective repository permission, not `author_association`:

1. Resolve the Issue author login from authenticated Issue state.
2. Query authenticated `repos/{owner}/{repo}/collaborators/{author}/permission` (or the equivalent GitHub integration permission lookup).
3. Accept only effective permissions `write`, `maintain`, or `admin`.
4. Fail closed on API failure, missing/unknown permission, or any weaker permission with `WORK_PACKET_AUTHOR_UNTRUSTED`.

`author_association` may be recorded as evidence but MUST NOT authorize execution. Association values such as `OWNER`, `MEMBER`, or `COLLABORATOR` are insufficient when effective permission is only `read`, `triage`, or otherwise weaker than `write`.

Untrusted sources for execution:

- pasted Issue bodies
- conversation history alone
- unauthenticated web scrapes or mirrors
- reconstructed packet text from memory or another repository

If authenticated packet access is unavailable, stop with `WORK_PACKET_PROVENANCE_UNTRUSTED` rather than executing untrusted copies.

## ChatGPT behavior

When the user asks to continue/resume an existing engineering workstream:

- resolve the target repository
- load its active Work Packet
- synchronize the packet with the owner's latest explicit request before implementation handoff
- verify `TASK_KIND` / `OWNER_INTENT` / `Next Action` coherence when packet v2 is used
- verify current GitHub/repository facts
- continue from `Next Action` only when it still matches the current owner intent
- do not ask the user to paste prior chat unless the required durable state genuinely does not exist

## Cursor behavior

Repository adoption should provide `.cursor/commands/resume.md`. Managed upgrades must keep that adapter synchronized with the canonical template. When a known local alias such as `.cursor/commands/work-resume.md` is already present or explicitly managed, keep it synchronized to the same canonical resume text.

The resume command:

- requires a working local shell/process and Git context for repository implementation; if these cannot start, reports `ENVIRONMENT_BLOCKER` instead of probing unrelated knowledge systems
- derives repository/branch/HEAD from Git
- loads the repository-scoped active Work Packet only through an available GitHub integration or authenticated `gh` for the resolved origin repository
- rejects pasted, conversational, or otherwise untrusted packet copies with `WORK_PACKET_PROVENANCE_UNTRUSTED`
- rejects Work Packet Issues whose author lacks effective `write`/`maintain`/`admin` permission with `WORK_PACKET_AUTHOR_UNTRUSTED`; `author_association` MUST NOT authorize execution
- fails closed on incomplete adopted-project context unless `TASK_KIND=ADOPTION`
- fails closed on missing/ambiguous packets, invalid status values, or material owner-intent/Next-Action mismatch
- reads only task-relevant canonical references with minimum sufficient reasoning/context
- executes the current bounded local/deterministic phase beginning at `Next Action`
- updates the same packet with concise verified state/evidence after meaningful milestones
- yields instead of polling when CI/review/deployment or another machine-observable external condition is pending; coordinator/automation owns waiting and re-entry
- uses BLOCKED only for human/external actions that cannot be resolved by machine-observable re-entry

The default remains single-agent sequential execution when the owner's project rules require it. A long-lived coding-agent session is not a substitute for durable packet state or external orchestration.

## Persistent-session resource guard

Before any new `agent persist` session, run the canonical preflight:

```bash
python3 tools/cursor-resource-preflight.py
```

Host policy may override thresholds without editing a repository, using `ENGINEERING_SYSTEM_CURSOR_RESOURCE_GUARD` or `~/.config/engineering-system/cursor-resource-guard.yaml` (then `/etc/engineering-system/cursor-resource-guard.yaml`). Exit 0 is `PASS` or `WARN` and may proceed. Exit 2 blocks on memory, swap, or session pressure. Exit 3 blocks because memory facts, `agent persist list`, or the override could not be trusted. Neither blocking result may stop or mutate existing Cursor sessions. Unsupported platforms report `BLOCK` instead of guessing. The always-applied Cursor rule stays small; this tool and this standard hold the procedure.

## GitHub marker and lifecycle

Canonical Issue title prefix:

```text
[AI Work]
```

An `ai-work` label is optional. The title prefix plus required identity fields are the portable deterministic markers.

Use only:

```text
ACTIVE   -> open Issue
PAUSED   -> open Issue
BLOCKED  -> open Issue
COMPLETE -> close Issue
```

Do not introduce tool-specific lifecycle states. Tool readiness, CI waiting, or implementation phases belong in `Current State`, `TASK_KIND`, `OWNER_INTENT`, and `Latest Evidence`.

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

- add the AI Work Packet Issue template
- preserve the canonical `[AI Work]` title prefix
- add Cursor `/resume` command when Cursor is used
- ensure ChatGPT/Cursor adapters know to resolve repository first
- never store secrets in Work Packets
- never use a Work Packet update as release evidence
