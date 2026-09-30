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

A manifest may define a top-level `setup_command` for deterministic CI dependency/bootstrap preparation. The shared affected-test workflow runs it once, and only when one or more scenarios were selected. Keep scenario `command` focused on the actual test invariant.

For a PR:
- scenarios tagged `pr` are cheap global guardrails and always run
- scenarios tagged `affected` run when their domains intersect changed-path domains
- unmatched changed paths widen selection conservatively rather than silently skipping validation

Ordinary PR validation must not duplicate the same native workflow through overlapping push+pull_request, and expensive release_gate scenarios must not be default affected/pr work or be invoked unconditionally by ordinary PR jobs; an expensive release-gate command must not be directly executed by an unconditional ordinary-PR job.

A project may keep its native affected-test selector; shared tooling must not duplicate it if both prove the same invariant.

### Domain quality

Affected-test economics depend on meaningful domains. Adoption should avoid mapping every complex repository path to a single `core` domain when clear component boundaries exist.

Prefer conservative mappings:
- top-level product components may become separate domains when the repository layout makes the boundary clear
- shared source/test paths may widen to multiple domains rather than incorrectly narrowing coverage
- domain-specific test commands should be recorded when they exist
- if only one broad project test command exists, it may cover all discovered domains, but that broad scope should be explicit rather than pretending to be fine-grained

### Build / lint / typecheck

Existing project-native build, lint, and typecheck commands are deterministic quality signals and may be represented as scenarios. Adoption may auto-wire them only when the repository already declares an unambiguous command (for example a package-manager script or Makefile target). Do not invent new linters/typecheckers merely to satisfy the Engineering System.

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

### Human-equivalent user-surface release tests — mandatory for user-facing products

Deterministic unit/component/API/matrix tests are necessary but do not reproduce the user's complete environment, discoverability, action sequence, state transitions, cross-surface continuity, realistic mistakes, failure diagnosis/recovery, and final user-visible outcome. They therefore cannot be the only release evidence for a user-facing product.

A user-facing release MUST use the human-equivalent gates as the semantic defect-discovery stages **before final exact-head CI and release-integrity qualification**:

```text
Surface Reconciliation / Feature-Scenario PASS1
 -> batched remediation
 -> PASS2 clean
 -> Full User E2E PASS1
 -> batched remediation
 -> PASS2 clean
 -> release-specific upgrade/platform qualification
 -> final exact-head automated CI
 -> release integrity / final audit
 -> owner/manual acceptance when required
 -> release authorization
```

PASS1 continues every safe independent scenario/journey after findings to maximize defect discovery. PASS1 findings are remediated as one bounded batch. PASS2 restarts the complete gate on the new candidate and requires zero mandatory findings. Do not interleave each finding fix with remote CI.

**Surface Reconciliation** exhaustively maps current product capability -> public user surface/control -> real scenario. It is breadth-first and checks discoverability, visible controls/actions, state-specific surfaces, terminology, error/recovery guidance, persistence/effective state, destructive safety, and cleanup.

**Full User E2E** is depth-first. It executes complete realistic user missions through the real primary product surface, proves actual outcomes, injects realistic mistakes/failures, performs user-visible diagnosis and recovery, exercises live edits/destructive lifecycle, and verifies cleanup/orphan truth.

Both gates:
- run on the same exact candidate HEAD;
- are independently required and never substitute for each other;
- require zero mandatory FAIL/PARTIAL/BLOCKED for release PASS;
- may reuse machine evidence for verification but not to replace the user action;
- must continue safe independent scenarios after a failure so one defect does not hide others;
- must retain run/evidence identity in the active release Work Packet.

For browser products, the user action MUST be performed by an actual Chromium/Chrome browser process. Playwright or an equivalent browser driver is allowed; headless Chromium/Chrome still counts as a real browser. jsdom/component tests, static DOM inspection, API-only flows, and CI contract checks do not count as execution PASS.

For CLI/desktop/mobile products, use the actual supported public primary interface with the same human-equivalent principle.

## Performance/resilience

```text
steady state -> baseline -> workload -> concurrent/mixed load -> controlled fault -> recovery -> verify steady state
```

Run these when the change touches performance/resilience boundaries or at the release-candidate gate.

## Default execution ladder

- development: affected L0-L5 only
- PR: affected scenarios + cheap `pr` guardrails
- optional nightly: broader deterministic/integration
- release closure discovery: breadth-first Feature/Scenario PASS1 -> batched remediation -> PASS2
- release user validation: Full User E2E PASS1 -> batched remediation -> PASS2
- release-specific qualification: upgrade/migration/platform/performance as applicable
- final release candidate: one exact-head automated deterministic/platform CI qualification
- integrity: artifact/hash/SBOM/provenance/manifest/attestation after final CI
- stable release: final exact-candidate audit, authorization, publication, then public smoke

A failure at an earlier mandatory level blocks starting downstream expensive qualification until the failure is resolved or explicitly classified as non-blocking.
