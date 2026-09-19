# Testing Standard

## Goal

Most defects should be discovered close to the change that introduced them. Expensive full-suite and operational E2E validation are release proofs, not the first place ordinary defects are found.

## Levels

- L0 Static / Contract
- L1 Unit
- L2 Component
- L3 Feature
- L4 Integration
- L5 Human UX
- L6 Lifecycle / Platform
- L7 Performance / Resilience
- L8 Operational E2E

## Change impact

```text
git diff
  -> changed paths
  -> affected domains
  -> selected scenarios
  -> execute cheapest relevant tests first
```

Deterministic CI must not depend on AI inference for test selection.

### Path mapping

`.engineering/tests.yaml` maps changed paths to domains. Scenarios declare domains and triggers.

For a PR:
- scenarios tagged `pr` are cheap global guardrails and always run
- scenarios tagged `affected` run when their domains intersect changed-path domains
- unmatched changed paths widen selection conservatively rather than silently skipping validation

A project may keep its native affected-test selector; shared tooling must not duplicate it if both prove the same invariant.

## Trigger semantics

- `affected`: selected when mapped domains are affected
- `pr`: cheap global PR guardrail only
- `preflight`: fast release-blocker check that must complete before expensive qualification
- `nightly`: optional broader recurring validation
- `rc`: release-candidate qualification
- `release`: exact-candidate release gate
- `post-release`: public/stable smoke or monitoring validation

Do not tag a multi-hour full suite as `pr` merely because it is deterministic.

## Regression policy

For a real bug:

```text
reproduce FAIL -> retain/add regression -> fix -> regression PASS -> affected tests PASS
```

Do not start a long full suite while a known blocking deterministic regression remains unresolved.

## Scenario metadata

Important scenarios should have stable IDs plus level, domains, triggers, platforms, invariants, origin, command, and release-gate metadata.

## Human UX

Test help/navigation, wizard inputs, invalid input recovery, copy/paste, wrong context, cancellation/EOF/Ctrl+C, generated remediation commands, and cross-output consistency. Run affected UX tests during development and broader UX qualification near release.

## Performance/resilience

```text
steady state -> baseline -> workload -> concurrent/mixed load -> controlled fault -> recovery -> verify steady state
```

Run these when the change touches performance/resilience boundaries or at the release-candidate gate.

## Default execution ladder

- development: affected L0-L5 only
- PR: affected scenarios + cheap `pr` guardrails
- optional nightly: broader deterministic/integration
- release preflight: cheap `preflight` blockers
- release candidate: full deterministic + selected L6/L7
- stable release: required L8 operational E2E on the exact candidate

A failure at an earlier mandatory level blocks starting downstream expensive qualification until the failure is resolved or explicitly classified as non-blocking.
