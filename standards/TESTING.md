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
- a path rule may also declare `invalidates: [SCENARIO-ID, ...]` for a non-local closure dependency; those target scenarios run even when their domains do not intersect, and each target must itself declare the `affected` trigger
- unmatched changed paths widen selection conservatively rather than silently skipping validation

### Change closure and direct invalidation

Domains express **behavioral ownership**. Direct invalidation expresses a different relation: changing one file can make another representation or derived artifact stale even when both belong to different domains.

Use `paths.<pattern>.invalidates` for deterministic non-local closure edges such as:

- a public-surface authority changing and therefore invalidating CLI/API/UI help, menus, generated docs, SDKs, or parity checks;
- source/test/release-input changes invalidating generated bundles, checksums, SBOM inputs, provenance, or release manifests;
- a schema/catalog change invalidating a generated fixture or contract snapshot.

The invalidated scenario is a **verification oracle**, not an auto-fix hook. Ordinary PR CI should fail when generated/derived state is stale; it must not silently rewrite the repository to make itself green. Refresh/regeneration happens during implementation, then the verification scenario proves closure.

Keep invalidation edges sparse and intentional. Do not use them as a second domain graph or point them at expensive release-only qualification. Targets must declare `affected` and should normally be cheap or medium closure checks.

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

For a real bug, use a two-phase strategy:

```text
CONVERGENCE
reproduce FAIL -> retain/add regression -> fix/coherent batch -> focused regression PASS -> affected/invalidated tests PASS -> repeat until affected findings are clean

CONFIRMATION
affected convergence clean -> broad/full regression once at the integration/qualification boundary
```

Do not restart a long full suite after every individual fix. Unchanged domains are not retested during convergence unless path/domain/invalidation evidence, shared-state coupling, uncertainty, or change risk says they may be affected. If affected mapping is incomplete or a failure is cross-cutting, widen conservatively.

Do not start a long full suite while a known blocking deterministic regression remains unresolved.

## Scenario metadata

Important scenarios should have stable IDs plus level, domains, triggers, platforms, invariants, origin, command, and release-gate metadata.

## Human UX

Test help/navigation, wizard inputs, invalid input recovery, copy/paste, wrong context, cancellation/EOF/Ctrl+C, generated remediation commands, and cross-output consistency. Run affected UX tests during development and broader UX qualification near release.

### Public-surface authority and parity

When one user-visible contract is represented in several places, the project must avoid independent hand-maintained truth.

Examples include:

- CLI catalog / grammar / help / completion / menu / documentation;
- API schema / router / SDK / examples / reference docs;
- UI route model / navigation / permissions / user help;
- configuration schema / generated samples / validation docs.

The project chooses the authority that fits its architecture; Engineering System does not impose a universal catalog format. Secondary representations must either be generated from that authority or have a deterministic parity check that fails when they drift. The canonical product/specification still defines intended behavior, while actual code/artifact/tested behavior remains the observed implementation truth.

For a structured public surface, add a cheap/medium affected scenario that proves the applicable parity/derivation invariant. When changes span domains, use direct invalidation edges so the closure scenario cannot be skipped.

### Surface Delta Reconciliation — development / PR gate

Do not wait until release to discover ordinary public-surface workflow drift.

For a user-facing change that adds or materially changes a capability, command, API action, UI control, configuration path, installer flow, or generated user guidance, perform a **bounded Surface Delta Reconciliation** for the changed slice during development/PR:

```text
changed feature
 -> current public discovery surface
 -> primary happy-path action or syntax
 -> inspect/show/readback
 -> misuse / wrong-context / error guidance
 -> edit/disable/delete/recovery semantics when applicable
 -> cross-output terminology/parity
```

This is L5 human-UX/change-closure evidence for the affected slice, not the exhaustive release gate. It may be represented as a stable `affected` scenario (normally cheap/medium) plus direct invalidation from all representations of that surface. The selected runtime should directly exercise the applicable public surface when human-equivalent interaction is available; deterministic tests remain supporting evidence.

A clean Surface Delta Reconciliation does **not** satisfy or reduce the mandatory release Surface Reconciliation scope. The release gate still reruns the complete product surface on the exact candidate HEAD.

### Human-equivalent user-surface release tests — mandatory for user-facing products

Deterministic unit/component/API/matrix tests are necessary but do not reproduce the user's complete environment, discoverability, action sequence, state transitions, cross-surface continuity, realistic mistakes, failure diagnosis/recovery, and final user-visible outcome. They therefore cannot be the only release evidence for a user-facing product.

A user-facing release MUST use the human-equivalent gates as the semantic defect-discovery stages **before final exact-head CI and release-integrity qualification**:

```text
fast release preflight
 -> Surface Reconciliation discovery/remediation/rerun until clean
 -> Full User E2E discovery/remediation/rerun until clean
 -> rerun Surface Reconciliation when E2E fixes changed the public surface
 -> both user gates PASS on the same exact HEAD
 -> candidate freeze
 -> release-specific upgrade/platform/performance qualification
 -> final exact-head automated CI
 -> release integrity / final audit
 -> owner acceptance / release authorization
 -> stable publication
 -> post-release public smoke
```

Before either user gate starts, read the repository-local canonical gate contract **in full** and bind the run to that contract; a wrapper/script/harness/CI-first substitute is invalid primary user evidence. Each discovery pass continues every safe independent scenario/journey after findings to maximize defect discovery. Freeze the complete finding set and finish the gate's final report/readback/offboarding. Then remediate the findings as one or more coherent bounded batches and use only the affected scenario/journey plus required invalidation/parity checks to prove each batch. Do **not** restart the complete gate after every individual fix or retest untouched journeys merely because source changed. Once every frozen actionable finding is targeted-clean and no affected validation exposes a new blocker, start one brand-new **complete confirmation run** with a new run identity. Targeted/affected reruns are regression evidence only and cannot themselves close the gate; the complete confirmation run proves that untouched areas did not regress. If that confirmation run discovers new findings, freeze that new set, return to targeted convergence, and then run one new complete confirmation again. Repeat with no fixed pass ceiling until the latest complete run is clean. Project-specific release contracts may require additional clean repeat passes. Do not interleave each finding fix with remote CI.

**Surface Reconciliation** exhaustively maps current product capability -> public user surface/control -> real scenario. It is breadth-first and checks discoverability, visible controls/actions, state-specific surfaces, terminology, error/recovery guidance, persistence/effective state, destructive safety, and cleanup.

**Full User E2E** is depth-first. It executes complete realistic user missions through the real primary product surface, proves actual outcomes, injects realistic mistakes/failures, performs user-visible diagnosis and recovery, exercises live edits/destructive lifecycle, and verifies cleanup/orphan truth.

Both gates:
- are executed and finally audited by **the runtime selected by the current execution profile**, directly acting as the applicable User/Operator/Admin persona;
- run on the same exact candidate HEAD;
- are independently required and never substitute for each other;
- require zero mandatory FAIL/PARTIAL/BLOCKED for release PASS;
- may reuse machine evidence for verification but not to replace the selected runtime's real user action;
- forbid a coding agent, alternate model, wrapper, scripted scenario replay, test harness, or CI job from impersonating the acting user or declaring the gate PASS;
- must continue safe independent scenarios after a failure so one defect does not hide others;
- freeze the complete finding set before batched remediation;
- must retain machine-readable ledger-derived run/evidence identity in the active release Work Packet.

If the selected runtime cannot execute a mandatory user action because the real environment or required interaction capability is unavailable, that scenario is BLOCKED; do not delegate it to another agent merely to manufacture PASS.

For browser products, The selected runtime MUST perform the user action through an actual Chromium/Chrome browser process. Playwright or an equivalent browser driver is allowed; headless Chromium/Chrome still counts as a real browser. jsdom/component tests, static DOM inspection, API-only flows, and CI contract checks do not count as execution PASS.

For CLI/desktop/mobile products, The selected runtime directly uses the actual supported public primary interface with the same human-equivalent principle. See `standards/USER_ACCEPTANCE.md` for the portable gate semantics and evidence contract.

## Performance/resilience

```text
steady state -> baseline -> workload -> concurrent/mixed load -> controlled fault -> recovery -> verify steady state
```

Run these when the change touches performance/resilience boundaries or at the release-candidate gate.

## Default execution ladder

- development: affected L0-L5 only
- PR: affected scenarios + cheap `pr` guardrails
- optional nightly: broader deterministic/integration
- release closure discovery: complete breadth-first Feature/Scenario discovery -> final report/offboard -> targeted affected remediation/reruns until frozen findings are clean -> one brand-new complete confirmation run -> repeat by finding batch until clean
- release user validation: complete Full User E2E discovery -> final report/offboard -> targeted affected journey remediation/reruns until frozen findings are clean -> one brand-new complete confirmation run -> repeat by finding batch until clean
- release-specific qualification: upgrade/migration/platform/performance as applicable
- final release candidate: one exact-head automated deterministic/platform CI qualification
- integrity: artifact/hash/SBOM/provenance/manifest/attestation after final CI
- stable release: final exact-candidate audit, authorization, publication, then public smoke

A failure at an earlier mandatory level blocks starting downstream expensive qualification until the failure is resolved or explicitly classified as non-blocking.
