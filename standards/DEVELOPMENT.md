# Development Standard

## Default flow

Request -> classify -> load context -> affected domains/contracts -> invariants -> implement -> affected tests -> wider gate if required -> diff review -> commit/PR

## Repository / worktree safety

Before coding:
- inspect repository status, branch/worktree, AGENTS.md, and .engineering metadata
- preserve unrelated/uncommitted work
- do not implement in the wrong worktree/branch
- record the starting revision for release-sensitive work

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

## Technical debt

Do not mix unrelated debt cleanup into urgent bug fixes. Record meaningful deferred debt rather than expanding current scope.

## ADR threshold

Use an ADR only for durable decisions such as architecture boundaries, public API/CLI contracts, security models, persistent formats, major runtime dependencies, or decisions expensive to reverse.