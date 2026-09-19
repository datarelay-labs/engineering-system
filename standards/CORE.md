# Core Engineering Standard

## Scope

The Engineering System governs the complete software lifecycle for every software/product engineering project the owner works on with ChatGPT or Cursor, regardless of GitHub organization, repository owner, product name, or project location.

This includes, but is not limited to, repositories under `datarelay-labs`, `xdr-labs`, and future related organizations or repositories.

The canonical Engineering System repository is `datarelay-labs/engineering-system`.

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
2. If the target repository has not yet adopted the Engineering System, identify the adoption gap and still follow the canonical standard by default.
3. Classify the change: FEATURE, BUGFIX, REFACTOR, SECURITY, PERFORMANCE, OPERATIONS, RELEASE, or DOCUMENTATION.
4. Identify affected domains, public contracts, persisted state, security boundaries, and operational impact.
5. Inspect relevant implementation, tests, docs, and historical regressions.
6. Make the smallest correct change; avoid unrelated scope/refactors.
7. Run affected tests before wider suites.
8. Bug fixes should include a regression that fails before the fix and passes after it whenever practical.
9. Never weaken a valid test merely to obtain PASS.
10. Never silently skip a required gate or report unexecuted work as PASS.
11. Historical evidence does not qualify a different source revision.
12. Public compatibility, data migration, security, upgrade, rollback, and documentation impact must be handled when relevant.
13. Operational failures should feed back into tests, runbooks, RCA, ADR, or requirements.

## Definition of done

A change is done only when intended behavior is implemented, affected tests pass, fixed defects are durably regressed when practical, relevant security/dependency/compatibility impacts are handled, public contracts/docs are updated when needed, operational/migration/rollback implications are handled when needed, and required deterministic evidence is recorded.

Release readiness is stricter and is defined by `RELEASE.md`.
