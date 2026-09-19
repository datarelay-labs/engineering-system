# Development Standard

## Default flow
```text
Request -> classify -> read context -> affected domains -> invariants
-> implement -> affected tests -> wider gate if required -> diff review -> commit/PR
```

## Before coding
- inspect repository status, branch/worktree, AGENTS.md, and .engineering metadata
- inspect existing implementation and tests
- identify affected domains and safety boundaries

## Implementation
Prefer small reviewable changes, existing abstractions, deterministic behavior, and explicit state transitions.
Avoid speculative scope expansion, unrelated refactoring, duplicated frameworks, and undocumented compatibility breaks.

## Bug fixes
```text
reproduce -> failing regression -> fix -> regression PASS -> affected suite PASS
```
If a deterministic regression is impractical, document why and retain another durable form of evidence.

## ADR threshold
Use an ADR only for durable decisions such as architecture boundaries, public API/CLI contracts, security models, persistent formats, major runtime dependencies, or decisions expensive to reverse.
