# Design Standard

## Purpose

Design is a lightweight gate between requirements/decisions and implementation. It prevents coding agents from inventing architecture, public contracts, persistence changes, security boundaries, or operational behavior while still avoiding heavyweight project-charter/WBS process.

The default is **minimum sufficient design**, not a mandatory long document.

## Minimal design gate

Before implementing a FEATURE, material REFACTOR, SECURITY change, public-contract change, persistence/schema change, or operational behavior change, establish:

1. **Goal** — the user/system outcome.
2. **Non-goals** — what is intentionally outside this change.
3. **Affected public contract** — API, CLI, config, installer, data format, integration, UX, or compatibility surface.
4. **State / migration impact** — persisted state, schema, upgrade, rollback, irreversibility.
5. **Security / operations impact** — trust boundaries, privileges, secrets, health, recovery, deployment.
6. **Architecture boundary** — components/interfaces that may change and those that must remain stable.
7. **Acceptance / regression criteria** — observable behavior and the tests/evidence that will prove it.

For a small local bug fix or documentation-only change, these items may be implicit if the affected contract and regression are already obvious from existing canonical artifacts.

## Where design state lives

Do not create a new permanent design document for every task.

Use the smallest durable destination that fits the decision:

- existing product/specification artifact for intended product behavior
- ADR for durable expensive-to-reverse architecture/security/persistence/public-contract decisions
- AI Work Packet for temporary current implementation coordination
- test invariant/regression for executable behavior
- runbook for operational procedure

Discussion that is not yet accepted remains discussion and must not be promoted automatically to canonical design.

## Design review trigger

Stop implementation and ask for the minimum missing decision when:

- Goal or non-goals are materially ambiguous.
- Two plausible designs change public behavior differently.
- A breaking compatibility decision is required.
- Data loss, irreversible migration, or new privileged/security behavior is possible.
- Production rollout/rollback behavior cannot be inferred safely.
- The change would create a new architectural subsystem or durable dependency without an accepted boundary.

Do not ask for owner input when existing canonical product/specification artifacts already resolve the decision.

## Design-to-development handoff

Implementation begins only after the minimal design gate is resolved.

The coding agent should then:

```text
design gate
 -> identify affected domains
 -> inspect implementation/tests
 -> smallest correct change
 -> affected deterministic tests
 -> wider qualification by risk
```

If implementation reveals that an accepted design assumption was wrong, stop, update the design decision, and then continue. Do not silently reshape the product during coding.

## Definition of sufficient design

Design is sufficient when another engineer or AI agent can state:

- what outcome is being built
- what is not being built
- which contracts/state/boundaries can change
- which compatibility/security/operational constraints apply
- how correctness will be demonstrated

Anything beyond that is optional unless project risk requires it.
