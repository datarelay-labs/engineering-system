# Core Engineering Standard

## Scope

The Engineering System governs the complete software lifecycle for every software/product engineering project the owner works on with AI assistance, regardless of GitHub organization, repository owner, product name, or project location.

The canonical Engineering System repository is `datarelay-labs/engineering-system`.

Repository onboarding/adoption is defined by `ADOPTION.md`.

requirements / decisions -> minimal design gate -> development -> review -> affected validation -> release qualification -> operations -> incident / RCA -> improvement / retirement

## Roles

- **Owner:** product requirements, scope, final decisions, release approval, human UX judgment.
- **Independent AI reviewer:** architecture, requirements, test strategy, independent review, incident analysis.
- **Coding agent:** repository inspection, implementation, tests, affected regression, evidence.
- **Automation:** deterministic validation, CI, security/performance checks, artifact verification.
- **GitHub:** durable source of truth for code, history, gates, and releases.
- **Wiki/Athena:** derived human-readable and AI-searchable knowledge; canonical Git content wins on conflict.

## Solo-developer efficiency principles

1. Use the cheapest deterministic check that can falsify correctness first.
2. Run affected tests before broad suites.
3. PR validation is fast/affected by default; full qualification is not a default PR gate.
4. Do not run duplicate shared/native gates that prove the same invariant.
5. A blocking deterministic failure stops downstream expensive qualification until fixed.
6. Full lifecycle/platform/performance/operational E2E belongs at release-candidate boundaries unless a change specifically requires earlier execution.
7. Keep AI context small and high-signal; load only task-relevant standards/specifications.
8. Add tooling only when it removes repeated manual work or materially improves correctness.

## Universal rules

1. Load `AGENTS.md` and `.engineering/project.yaml` first.
2. Load test/release metadata and standards only when relevant to the task.
3. If the target repository has not yet adopted the Engineering System, identify the adoption gap and still follow the canonical standard by default.
4. Classify the change: FEATURE, BUGFIX, REFACTOR, SECURITY, PERFORMANCE, OPERATIONS, RELEASE, or DOCUMENTATION.
5. For material design-bearing changes, apply the minimal gate in `DESIGN.md` before implementation.
6. Identify affected domains, public contracts, persisted state, security boundaries, and operational impact.
7. Inspect relevant implementation, tests, docs, and known regressions.
8. Make the smallest correct change; avoid unrelated scope/refactors.
9. Bug fixes should include a regression that fails before the fix and passes after it whenever practical.
10. Never weaken a valid test merely to obtain PASS.
11. Never silently skip a required gate or report unexecuted work as PASS.
12. Before merge or terminal completion, inspect machine-observable PR review feedback. Every actionable finding from a human reviewer or configured automated reviewer must be fixed and revalidated, or explicitly dispositioned with concise evidence showing why it is non-actionable, out of scope, or incorrect. A COMMENTED/advisory review state is not itself PASS or FAIL; the content controls. Do not merge or complete while actionable review feedback remains unaddressed.
13. Historical evidence does not qualify a different source revision.
14. Public compatibility, data migration, security, upgrade, rollback, and documentation impact must be handled when relevant.
15. Operational failures should feed back into tests, runbooks, RCA, ADR, or requirements.

## Definition of done

A normal change is done when intended behavior is implemented, affected tests pass, fixed defects are durably regressed when practical, actionable review feedback is addressed or explicitly dispositioned with evidence, and relevant contract/security/operational documentation is updated.

Release readiness is stricter and is defined by `RELEASE.md`.
