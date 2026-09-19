# Testing Standard

## Goal
Most defects should be discovered close to the change that introduced them. Full operational E2E is a release proof, not the first place ordinary defects are found.

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
git diff -> changed paths -> affected domains -> dependent domains -> selected scenarios -> execute
```
Start with explicit mappings; deterministic CI should not depend on AI inference.

## Regression policy
For a real bug: reproduce, add/identify failing regression, fix, prove PASS, and retain the scenario.

## Scenario metadata
Important scenarios should have stable IDs plus level, domains, triggers, platforms, invariants, origin, command, and release_gate metadata.

## Property/stateful testing
Use selectively for lifecycle state machines, parsers/configuration, policy evaluation, allocators, and input validation.

## Human UX
Test help/navigation, wizard inputs, invalid input recovery, copy/paste, wrong context, cancellation/EOF/Ctrl+C, generated remediation commands, and cross-output consistency.

## Performance/resilience
```text
steady state -> baseline -> workload -> concurrent/mixed load -> controlled fault -> recovery -> verify steady state
```

## Trigger guidance
- development: affected L0-L5
- PR: affected plus wider deterministic regression
- nightly/major change: integration + selected lifecycle/platform
- release candidate: full deterministic + platform/lifecycle + performance
- stable release: operational E2E on exact candidate
