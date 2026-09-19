# Core Engineering Standard

## Scope

The Engineering System governs the complete software lifecycle for adopted Data Relay Labs projects:

requirements / decisions -> development -> review -> testing / regression -> security / dependency control -> release / upgrade / rollback -> operations / observability -> incident / RCA -> improvement / deprecation / retirement

## Roles

- **Owner:** product requirements, scope, final decisions, release approval, human UX judgment.
- **ChatGPT / independent AI reviewer:** architecture, requirements, test strategy, independent review, incident analysis.
- **Cursor / coding agent:** repository inspection, implementation, tests, affected regression, evidence.
- **Automation:** deterministic validation, CI, security/performance checks, artifact verification.
- **GitHub:** durable source of truth for code, history, gates, and releases.
- **Wiki:** derived human-readable and AI-searchable knowledge; Git remains canonical when the repository defines the rule.

## Universal rules

1. Load mandatory engineering context before implementation.
2. Classify the change: FEATURE, BUGFIX, REFACTOR, SECURITY, PERFORMANCE, OPERATIONS, RELEASE, or DOCUMENTATION.
3. Identify affected domains, public contracts, persisted state, security boundaries, and operational impact.
4. Inspect relevant implementation, tests, docs, and historical regressions.
5. Make the smallest correct change; avoid unrelated scope/refactors.
6. Run affected tests before wider suites.
7. Bug fixes should include a regression that fails before the fix and passes after it whenever practical.
8. Never weaken a valid test merely to obtain PASS.
9. Never silently skip a required gate or report unexecuted work as PASS.
10. Historical evidence does not qualify a different source revision.
11. Public compatibility, data migration, security, upgrade, rollback, and documentation impact must be handled when relevant.
12. Operational failures should feed back into tests, runbooks, RCA, ADR, or requirements.

## Engineering domains

The shared system is organized into CORE, DEVELOPMENT, QUALITY, SECURITY, RELEASE, OPERATIONS, KNOWLEDGE, and AI / ENFORCEMENT.
`TESTING.md` provides detailed test-level mechanics under QUALITY.

## Definition of done

A change is done only when intended behavior is implemented, affected tests pass, fixed defects are durably regressed when practical, relevant security/dependency/compatibility impacts are handled, public contracts/docs are updated when needed, operational/migration/rollback implications are handled when needed, and required deterministic evidence is recorded.

Release readiness is stricter and is defined by `RELEASE.md`.