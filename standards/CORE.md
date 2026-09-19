# Core Engineering Standard

## Roles
- **Owner:** product requirements, scope, final decisions, release approval, human UX judgment.
- **ChatGPT / independent AI reviewer:** architecture, requirements, test strategy, independent review, incident analysis.
- **Cursor / coding agent:** repository inspection, implementation, tests, affected regression, evidence.
- **Automation:** deterministic validation, CI, security/performance checks, artifact verification.
- **GitHub:** durable source of truth for code, history, gates, and releases.

## Universal rules
1. Read project-local context before editing.
2. Classify the change: FEATURE, BUGFIX, REFACTOR, SECURITY, PERFORMANCE, OPERATIONS, RELEASE, or DOCUMENTATION.
3. Identify affected domains and public contracts before implementation.
4. Inspect relevant implementation and tests.
5. Make the smallest correct change; avoid unrelated refactors.
6. Run affected tests before wider suites.
7. Bug fixes should include a regression that fails before the fix and passes after it whenever practical.
8. Never weaken a valid test merely to obtain PASS.
9. Never silently skip a required gate or report unexecuted work as PASS.
10. Historical test evidence does not qualify a different source revision.
11. Release artifacts use immutable source identity when the project profile requires it.
12. Operational failures should feed back into tests, runbooks, or ADRs.

## Definition of done
A change is done when intended behavior is implemented, required affected tests pass, regressions are captured, public contracts/docs are updated when needed, and exact evidence is reported.
