# AI Agent Base Rules

Applies to ChatGPT, Cursor, Codex, and similar agents.

## Before editing
1. Read repository `AGENTS.md`.
2. Read `.engineering/project.yaml`, `tests.yaml`, and `release.yaml` when present.
3. Inspect repository status, branch/worktree, relevant code, and relevant tests.
4. Classify the change.
5. Identify affected domains and public contracts.
6. Preserve unrelated work and user data.

## Implementation
- make the smallest correct change
- do not silently expand scope
- avoid unrelated refactors
- follow repository patterns unless architecture intentionally changes
- preserve public behavior unless requirements change it
- add regression coverage for bugs
- never weaken valid assertions merely to get PASS

## Testing
- run affected tests first
- expand based on risk
- use actual public interfaces for user-behavior E2E
- distinguish deterministic PASS from AI opinion
- report required checks that are skipped or blocked

## Release
- identify exact candidate SHA
- never reuse evidence from another SHA
- prefer immutable artifact references
- do not tag/release/change stable channels without authorization
- reset exact-head qualification when product code changes

## Reporting
Report start/final SHA, files changed, tests run, exact failures/blockers, and unresolved release blockers. Never report unexecuted checks as PASS.
