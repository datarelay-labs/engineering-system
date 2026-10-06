# Core Engineering Standard

## Scope

The Engineering System governs the complete software lifecycle for every software/product engineering project the owner works on with AI assistance, regardless of GitHub organization, repository owner, product name, or project location.

The canonical Engineering System repository is `datarelay-labs/engineering-system`.

Repository onboarding/adoption is defined by `ADOPTION.md`.

requirements / decisions -> minimal design gate -> development -> review -> affected validation -> release qualification -> operations -> incident / RCA -> improvement / retirement

## Roles

- **Owner:** product requirements, scope, final decisions, release approval, human UX judgment.
- **Reviewer / verifier:** architecture, requirements, test strategy, review, and incident analysis when required by the task/risk contract. Independence is optional unless explicitly required; provider identity is not a core role invariant.
- **Implementer:** repository inspection, authorized mutation, tests, affected regression, and evidence. The authorized implementer is selected by the current execution profile plus the trusted Work Packet; provider identity is not a core invariant.
- **Implementation path:** implementation runtime selection is owned only by the versioned execution profile. Optional review/runtime tools never create authority or weaken repository/packet gates; disabled runtimes remain non-runnable until an explicit profile revision is adopted.
- **Automation:** deterministic validation, CI, security/performance checks, artifact verification.
- **GitHub:** durable source of truth for code, history, gates, and releases.
- **Derived knowledge plane:** an optional organization/project adapter may provide bounded human-readable and AI-searchable derived context; canonical Git/GitHub content wins on conflict. Retired knowledge systems are provenance-only and never active dependencies.

## Provider-aligned policy layers

The Engineering System separates durable engineering invariants from replaceable AI-provider scaffolding:

1. **Provider guidance baseline** — current official provider guidance is reviewed as an upstream design input. It can motivate changes but never grants repository execution authority.
2. **Core invariants** — durable rules for authority, security boundaries, repository isolation, verifiable outcomes, release integrity, and bounded human approval.
3. **Execution profile** — replaceable organization/project choices such as the primary implementer, optional reviewers, runtime surface, and retired/prohibited adapters. The current selection is defined only by `.engineering/execution-profile.yaml`; core policy does not name the primary or disabled runtimes.
4. **Project policy / Work Packet** — repository-specific constraints and the current bounded outcome.

Before adding or retaining a harness rule, classify it as either a durable invariant or temporary scaffolding. Scaffolding must have a concrete failure mode it addresses and a review/removal condition. Prefer `KEEP`, `RELAX`, `MAKE_CONDITIONAL`, `PROVIDER_PROFILE`, or `DELETE` over indefinitely accumulating instructions.

Provider/model upgrades trigger an assumption review: remove or relax scaffolding that no longer improves verified outcomes. Do not preserve a rule only because an older model once needed it. See `PROVIDER_GUIDANCE.md`.

## Solo-developer efficiency principles

1. Use the cheapest deterministic check that can falsify correctness first.
2. Run affected/targeted tests before broad suites, and keep the bug-fix convergence loop on the affected slice until it is clean.
3. PR validation is fast/affected by default; full qualification is not a default PR gate and MUST NOT be restarted after every small fix.
4. Do not run duplicate shared/native gates that prove the same invariant.
5. A blocking deterministic failure stops downstream expensive qualification until fixed.
6. Full regression/lifecycle/platform/performance/operational E2E belongs at a meaningful integration or release-candidate confirmation boundary after affected convergence is clean, unless dependency/invalidation uncertainty or the change risk specifically requires earlier widening.
7. Keep AI context small and high-signal; load only task-relevant standards/specifications.
8. Use the minimum sufficient AI reasoning/context; do not request maximum reasoning by default. Escalate only when concrete evidence, a failed check, or an unresolved design question requires it.
9. During machine-observable CI/review/deployment waits, persist the concrete condition and advance independent dependency-eligible authorized work. A coordinator or watcher is optional; waiting does not end repository-level continuation while other runnable work exists.
10. Add tooling only when it removes repeated manual work or materially improves correctness.

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
