# AI Agent Base Rules

These are agent-agnostic base rules for ChatGPT, Cursor, Codex, and similar agents. Product-specific adapters may add tool syntax, but must not weaken the core rules.

## Context budget

Always load:
1. repository `AGENTS.md`
2. `.engineering/project.yaml`

Load only when relevant:
- `.engineering/tests.yaml` for implementation/debugging/testing
- `.engineering/release.yaml` for release/version/artifact work
- the single relevant Engineering System standard
- Product Master/OpenSpec/ADR/runbook material only when the task touches that contract

Do not preload the entire Wiki, all standards, archived specifications, or historical discussions. Use minimum sufficient reasoning/context and do not request maximum reasoning by default; escalate only for a concrete blocker, failed check, or unresolved design question.

## Design

Before implementing material FEATURE, REFACTOR, SECURITY, public-contract, persistence/schema, or operational behavior changes, apply the minimal design gate in `standards/DESIGN.md`. Reuse existing canonical product/specification decisions instead of creating redundant design documents.

## Incident / operational failure

When the user reports an outage, degraded service, failed upgrade, data-loss risk, or other production-impacting symptom, read `standards/OPERATIONS.md` and enter the incident lifecycle. Preserve evidence first; do not perform destructive or irreversible recovery without explicit owner approval unless an approved runbook already authorizes it.

## Repository adoption

When the user asks to apply/adopt/bootstrap the Engineering System to a repository, read `standards/ADOPTION.md` and treat adoption as its own bounded workstream. Inventory first, preserve project-specific invariants, classify existing AI/engineering rules, prefer `tools/adopt.py` for mechanical installation, and fail closed on ambiguous test/release commands or destructive overwrite. If the repository is already pinned to an older managed Engineering System version, use the managed upgrade workflow instead of rerunning initial bootstrap. Do not mix adoption with unrelated product changes.

## Resume / session continuity
When the user asks to continue or resume existing engineering work, resolve the target repository first and read only that repository's active AI Work Packet before asking the user to reconstruct prior chat. Never scan unrelated repositories after the target repo is resolved. Verify current branch/HEAD/state independently; packet state is coordination context, not runtime or release authority. Fail closed if packet selection is missing or ambiguous. Before implementation handoff, synchronize the packet with the owner's latest explicit request: canonical STATUS only, current TASK_KIND/OWNER_INTENT for packet v2, and a Next Action that directly advances them. Do not hand off a stale release/cleanup action when the owner is asking for development/testing, and do not invent readiness statuses such as CURSOR_READY. See `standards/SESSION_CONTINUITY.md`.

## Before editing
- inspect repository status, branch/worktree, relevant code, and relevant tests
- classify the change
- identify affected domains and public contracts
- preserve unrelated work and user data

## Implementation
- make the smallest correct change
- do not silently expand scope
- avoid unrelated refactors
- preserve public behavior unless requirements change it
- add regression coverage for bugs
- never weaken valid assertions merely to get PASS

## Testing
- run the cheapest affected deterministic tests first
- expand based on risk
- do not run an expensive full suite when a known blocking deterministic regression already exists
- do not duplicate equivalent native CI gates
- use actual public interfaces for user-behavior E2E
- distinguish deterministic PASS from AI opinion
- report skipped/blocked required checks
- do not spend coding-agent model time polling CI/review/deployment waits; persist concise waiting state and yield to coordinator/automation

## Release
- run a fast release preflight before expensive qualification
- identify exact candidate SHA
- never reuse evidence from another SHA
- prefer immutable artifact references
- stop expensive downstream stages after a blocker
- do not tag/release/change stable channels without authorization
- reset exact-head qualification when product code changes

## Reporting
Report start/final SHA, files changed, tests run, exact failures/blockers, and unresolved release blockers. Never report unexecuted checks as PASS.
