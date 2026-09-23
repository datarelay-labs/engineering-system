# Development Standard

## Default flow

Request -> classify -> load context -> minimal design gate when required -> affected domains/contracts -> invariants -> implement -> affected tests -> wider gate if required -> diff review -> commit/PR

## Repository / worktree safety

Before coding:
- inspect repository status, branch/worktree, AGENTS.md, and .engineering metadata
- preserve unrelated/uncommitted work
- do not implement in the wrong worktree/branch
- record the starting revision for release-sensitive work

## Design handoff

For FEATURE, material REFACTOR, SECURITY, public-contract, persistence/schema, or operational behavior changes, apply `DESIGN.md` before implementation. Do not create a heavyweight design document when the required decisions already exist in canonical product/specification artifacts.

## Implementation

Prefer small reviewable changes, existing abstractions, deterministic behavior, explicit state transitions, and backward compatibility unless a break is intentional.

Avoid speculative scope expansion, unrelated refactors mixed into fixes, duplicated frameworks, silent contract changes, and changing tests to match accidental implementation behavior.

## Bug fixes

reproduce -> failing regression -> fix -> regression PASS -> affected suite PASS

If deterministic reproduction is impractical, explain why and retain another durable form of evidence.

## Refactoring

A refactor must preserve externally observable behavior unless behavior change is explicitly part of the request. Use existing regression evidence to prove preservation.

## Public compatibility

Changes to API, CLI, config, installer, persisted state, file formats, or automation contracts must identify:
- what remains compatible
- what is intentionally deprecated/broken
- migration/upgrade behavior
- fallback/rollback behavior when applicable

Breaking changes require an explicit product/release decision.

## Schema / data / configuration migration

When persisted state or schema changes:
- define forward migration
- define failure behavior
- define backup/rollback or irreversibility
- test representative old-to-new paths
- avoid silent destructive conversion

## Dependencies

Dependency additions/upgrades must satisfy `SECURITY.md` and include compatibility/regression validation. Do not add dependencies merely for convenience when native/existing capability is adequate.

## Scope discoveries during implementation

Implementation and review frequently expose adjacent problems. Discovery does not automatically expand scope.

- If the finding is required to satisfy the active outcome or its completion contract, keep it in the current Work Packet and re-size only when the change is material.
- If it is independently releasable or has a different owner, approval boundary, rollback boundary, or validation oracle, create/update a linked follow-up Issue/Work Packet and continue the current outcome.
- Preserve concise reproduction/evidence and affected paths; do not dump raw conversation or logs into the follow-up.
- Do not perform opportunistic refactors merely because the files are already open.
- A growing list of follow-ups is a planning/backlog signal, not permission to turn the current packet into a multi-domain program.

## Technical debt

Do not mix unrelated debt cleanup into urgent bug fixes. Record meaningful deferred debt rather than expanding current scope.

## ADR threshold

Use an ADR only for durable decisions such as architecture boundaries, public API/CLI contracts, security models, persistent formats, major runtime dependencies, or decisions expensive to reverse.