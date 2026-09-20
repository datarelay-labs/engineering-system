# Quality Standard

Quality covers correctness, regression safety, usability, compatibility, resilience, and release evidence.

## Required quality loop

```text
design gate when needed
 -> change
 -> build/lint/typecheck when applicable
 -> affected tests
 -> regression
 -> wider qualification by risk
 -> evidence
```

Use existing project-native build/lint/typecheck commands when they are meaningful. The Engineering System does not require adding a new tool merely to fill a checklist.

## Test model

Use the L0-L8 model defined in `TESTING.md`. Projects choose applicable levels but must not skip a required level silently.

## Defect handling

For BUGFIX work:
1. reproduce the defect
2. create or identify a regression that exposes it when practical
3. implement the smallest correct fix
4. prove the regression passes
5. run affected tests
6. retain the regression permanently
7. update runbook/ADR only when the defect reveals an operational or architectural gap

## Flaky tests

A flaky required test is a defect, not a reason to ignore the gate.
- do not repeatedly rerun until green and call it PASS
- capture the failure evidence
- quarantine only when the project explicitly records owner, reason, and restoration criteria
- release-critical coverage must have a deterministic replacement before quarantine is accepted

## Compatibility quality

When a change touches public API, CLI, configuration, data format, installer behavior, or persisted state:
- identify the compatibility contract
- test old-to-new paths when supported
- test rejection/error behavior for unsupported combinations
- update deprecation/upgrade documentation when behavior changes

## Human UX quality

Applicable user-facing products must test success paths plus misuse and recovery:
- invalid input
- wrong context
- copy/paste
- cancel / EOF / Ctrl+C
- stale/generated guidance
- cross-output consistency

## Performance and resilience

Use:
```text
steady state -> baseline -> load -> concurrent/mixed operation -> fault -> recovery -> steady state
```

Do not invent arbitrary thresholds. Use documented requirements/SLOs when present and always enforce safety invariants.

Canonical testing details are in `TESTING.md`.
