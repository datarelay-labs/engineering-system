# Release Standard

## Principle
A release is qualified for an exact source revision and the artifacts produced from it. A PASS from another revision cannot be reused.

## Default lifecycle
```text
candidate freeze -> deterministic regression -> build artifacts -> provenance/hashes
-> platform/lifecycle qualification -> performance/resilience -> operational E2E
-> release decision -> immutable tag/release -> public smoke
```

## Required concepts
Projects define as applicable: version source of truth, candidate SHA, channel, artifact SHA256, release manifest, SBOM, qualification result, rollback strategy, and public smoke validation.

## Exact-HEAD rule
When `exact_head_required: true`:
- product/dependency changes after qualification invalidate qualification
- a new merge commit must not be treated as already qualified unless project policy explicitly allows it
- do not tag an unqualified commit as the qualified candidate

## Artifact identity
Prefer immutable references. Avoid `main`, `latest`, or future/nonexistent tags when immutable candidate identity is required.

## Default blockers
- unresolved P0
- unresolved P1
- unresolved user-blocking P2
- required release gate failure
- provenance mismatch
- missing required platform/operational qualification

Build/qualification and publication are separate steps.
