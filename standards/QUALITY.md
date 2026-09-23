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

## Change risk and verification depth

Change kind describes what is being changed; change risk determines how much independent evidence is required. Risk does **not** determine roadmap priority.

Use `CHANGE_RISK=LOW|MEDIUM|HIGH|CRITICAL` as a compact coordinator signal, based on the combination of:
- production reach/blast radius;
- security/privilege/trust-boundary impact;
- persisted-data or migration impact;
- public API/CLI/config/compatibility impact;
- reversibility/rollback quality;
- cross-service or shared-infrastructure scope.

Default behavior:

- `LOW` — targeted deterministic validation and normal diff/review.
- `MEDIUM` — affected regression plus normal independent review where configured.
- `HIGH` — fresh-context independent verifier, wider affected qualification, and explicit rollback/compatibility/security evidence as applicable.
- `CRITICAL` — HIGH requirements plus explicit human approval for destructive/external/prod execution when required by the security/release contract and release/operational evidence appropriate to the change.

These are verification-depth defaults, not a replacement for task-specific mandatory gates. A seemingly small diff may be HIGH/CRITICAL if its blast radius or irreversibility is large.

## Bounded hardening and audit depth

Security, reliability, quality, and architecture can always be improved further. A quality process therefore needs a stopping rule as well as a defect-finding rule.

- Translate open-ended requests such as “harden this,” “audit everything,” or “make it robust” into a finite threat/risk surface and explicit completion contract before implementation.
- Required risk classes and known concrete failures belong in the current packet; speculative defense-in-depth and optional improvements become follow-up work once the required oracle is green.
- Do not treat the number of newly discovered low-severity opportunities as evidence that the current packet should remain active forever.
- After the completion contract passes and an independent review finds no blocking defect, additional broad/deeper audit is a new scope decision.
- Apply the sufficiency/depth-budget contract in `SESSION_CONTINUITY.md`; exceptions require concrete safety, data-integrity, incident, or release-blocking evidence.

## Architecture and quality entropy

Higher agent throughput must not be allowed to multiply local inconsistencies.

For repositories with meaningful architectural boundaries:
- encode important layering/dependency/public-boundary invariants as deterministic checks when practical;
- prefer existing canonical abstractions over near-duplicate helpers/frameworks;
- treat repeated exceptions, duplicated patterns, boundary violations, and stale compatibility shims as quality debt;
- periodically convert accumulated drift into small, targeted cleanup Work Packets/PRs rather than mixing broad cleanup into feature work;
- do not use generated LOC or number of cleanups as the quality metric; use reduced violations, rework, regressions, and review friction.

## Performance and resilience

Use:
```text
steady state -> baseline -> load -> concurrent/mixed operation -> fault -> recovery -> steady state
```

Do not invent arbitrary thresholds. Use documented requirements/SLOs when present and always enforce safety invariants.

Canonical testing details are in `TESTING.md`.
