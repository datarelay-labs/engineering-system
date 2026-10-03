# User Acceptance Standard

## Purpose

Every user-facing product requires two distinct human-equivalent quality gates before a release candidate may be frozen:

1. **Surface Reconciliation** — breadth-first proof that supported capabilities map coherently to the actual public user surface.
2. **Full User E2E** — depth-first proof that realistic users can complete supported missions and observe the intended real outcomes.

Project-specific scenario catalogs stay in the project repository. This standard defines portable execution semantics; it does not prescribe product-specific commands, pages, hosts, or topology.

## Mandatory executor

**ChatGPT itself is the executor and final auditor of both user gates.**

ChatGPT MUST directly act as the applicable real user personas — for example End User, Operator, Administrator, Incident Responder, or Platform Maintainer — and directly drive the supported public product surface.

A coding agent, alternate model, wrapper, shell script, browser test script, unit/integration suite, CI job, or other automated harness MUST NOT impersonate the acting user or declare either gate PASS.

Automation may drive a browser or collect supporting evidence, but ChatGPT must own the persona goal, public-surface interaction, observation, recovery decisions, scenario disposition, and final gate judgment.The required authority is:

```text
USER_ACCEPTANCE_EXECUTOR=CHATGPT
USER_ACCEPTANCE_FINAL_AUDITOR=CHATGPT
CHATGPT_DIRECT_PERSONA_EXECUTION=REQUIRED
ALTERNATE_AGENT_USER_EXECUTION=FORBIDDEN
SCRIPTED_USER_SCENARIO_EXECUTION=FORBIDDEN
AUTOMATED_HARNESS_ROLE=SUPPLEMENTAL_ONLY
```

If ChatGPT cannot perform a mandatory real user action because the required environment or interaction capability is unavailable, that scenario is BLOCKED. It is never delegated to another agent merely to obtain PASS.

## Applicability

This standard applies when `.engineering/project.yaml` declares `project.user_facing: true`.

The release profile must use `human_equivalent_user_tests.contract_version: 2`, reference repository-local contracts for both gates, require the same candidate, and require user-quality convergence before candidate freeze.

Machine/unit/component/API/static checks may support the gates but never substitute for the actual user action.

## Contract-first execution hard gate

Before **either** user gate starts, ChatGPT MUST resolve the repository-local canonical contract referenced by the release profile and read the **entire current contract end-to-end**. This happens before scenario execution, wrapper/harness execution, source/parser inspection, CI inspection, or an improvised checklist. The purpose is to bind execution to the project's actual personas, scope, prohibited actions, evidence rules, cleanup, invalidation, and PASS/FAIL/BLOCKED semantics rather than to the executor's remembered or guessed version of the test.

A run is invalid as user-gate evidence when it starts from a wrapper/scripted replay, generic test harness, source/test oracle, or CI shortcut before the canonical contract has been fully read and adopted for that run. Automation remains supporting evidence/orchestration only; it never becomes the acting persona merely because it covers many commands or scenarios.

A standalone request for one gate authorizes only that gate. Chaining Surface Reconciliation into state-changing Full User E2E requires either the current release-quality workflow/Work Packet to require the next gate or an explicit Full User E2E/release-qualification request.

Managed adoption/upgrade MUST NOT infer these contract semantics merely from the existence of contract paths. Writing compliance declarations for contract-first execution, complete rerun after remediation, or wrapper non-substitution requires an explicit review acknowledgement made only after both repository-local gate contracts have been inspected and updated as needed.

## Shared execution semantics

Both gates MUST bind to an exact candidate HEAD/build and exact committed test contract, use the actual supported primary public surface, use ChatGPT-led persona execution rather than a hidden/scripted answer key, and separate acting-user knowledge from auditor/source/test oracle knowledge.Both gates MUST also:

- continue every safe independent scenario after a finding;
- freeze the complete bounded finding set before remediation;
- remediate findings in a bounded batch instead of alternating each finding with expensive release qualification;
- restart invalidated coverage after source/public-surface/contract changes;
- retain machine-readable scenario/finding ledgers and derive summaries from those ledgers;
- record blocked/not-applicable work honestly rather than converting it to PASS;
- require zero mandatory FAIL/PARTIAL/BLOCKED results and zero unresolved blocking findings for release PASS;
- preserve evidence and clean up only run-owned state/processes.

Parallel execution is preferred whenever independent lanes are isolated. It must never corrupt shared product state or evidence.

For browser products, ChatGPT must perform the user action through a real Chromium/Chrome process. Playwright or another browser driver is allowed as the interaction mechanism because the real browser is still the public surface. Component rendering, jsdom, API-only checks, screenshots without interaction, and static DOM checks are supporting evidence only.

For CLI/desktop/mobile/mixed/other surfaces, ChatGPT directly uses the corresponding actual supported public interface.

## Gate A — Surface Reconciliation

Surface Reconciliation is **feature-first and black-box-first**.

Start from the supported product capability inventory, not from the implementation's command/control list. For each supported capability reconcile product capability, public surface/control, discovery path, role/context, user goal, lifecycle/scenario, error/empty/recovery guidance, result, and evidence.The ChatGPT acting persona discovers the product through the public surface first. Source, parser, route, component, generated metadata, internal catalog, test code, and scenario oracle knowledge may be used only as post-hoc auditor evidence after the corresponding public-surface evidence is frozen.

Surface Reconciliation checks, as applicable:

- supported capability with no usable public surface;
- public control with no current supported capability;
- hidden, legacy, duplicate, or undiscoverable paths;
- terminology, structure, procedure, role, context, empty-state, and error drift;
- status/version/provenance contradictions;
- destructive-action risk, confirmation, and recovery contracts;
- stale generated guidance, installer output, recovery text, examples, or documentation;
- workflow dead ends and missing next actions;
- direct vs AI-assisted guidance parity when the product claims an AI-assisted user path.

Surface Reconciliation SHOULD be runtime non-destructive when the product can prove the surface contract without mutation. If mutation is intrinsically required to expose a surface state, use isolated disposable/namespaced state and cleanup evidence; do not turn this gate into a substitute Full User E2E.

Release PASS requires complete mandatory capability/public-surface disposition plus `capability_coverage_pct=100` and `public_surface_coverage_pct=100`.## Gate B — Full User E2E

Full User E2E is **mission-first, black-box, and real-effect**.

For each applicable supported mission, ChatGPT assumes the applicable user role and maps:

```text
PRODUCT_CAPABILITY
 -> USER / OPERATOR / ADMIN GOAL
 -> PUBLIC USER ACTIONS
 -> STATE TRANSITIONS
 -> REAL OUTCOME / TRAFFIC / PERSISTED EFFECT
 -> NEGATIVE / MISTAKE / RECOVERY VARIANT
 -> FINAL USER-VISIBLE STATE
 -> RESULT / EVIDENCE
```

The acting persona begins without source/test/manual answer-key knowledge and learns the workflow from the product's public UX and user-visible guidance. Auditor contracts may detect omissions after discovery; they are not the acting user's script.

Full User E2E MUST, as applicable:

- begin from a known clean or explicitly namespaced starting state;
- pin installed/runtime candidate identity;
- execute real state changes and real user outcomes rather than internal-state substitutes;
- verify persistence/read-back/effective runtime truth where state is persisted;
- inject realistic invalid input, wrong context, cancellation, stale references, duplicate actions, and recovery;- repeat stateful/high-risk workflows across meaningfully different starting state/order/retry/concurrency conditions when one successful execution could hide stale-state, idempotency, or race defects;
- exercise concurrency, failure/recovery, and function-under-load when those are part of the product claim or risk surface;
- use the available suitable test estate without requiring hypothetical new infrastructure merely to become PASS-eligible;
- distinguish product failure from environment/tooling/management-path blockage;
- maintain a run-owned process/session/resource registry when external helpers or disposable state are created;
- prove cleanup/orphan truth at the end of the run.

Numeric performance thresholds MUST come from an explicit product profile/SLO. When no numeric SLO exists, measure and report without inventing a numeric PASS threshold; functional failure under load remains FAIL.

Release PASS requires complete mandatory mission disposition plus `use_case_coverage_pct=100`, `real_effect_coverage_pct=100`, and cleanup PASS.

## Finding-discovery and remediation loop

A finding is not automatically a stop condition.

For each gate:

```text
read the complete current canonical gate contract
 -> execute complete safe discovery pass
 -> retain all findings/evidence
 -> freeze ledgers/counters
 -> final gate report/readback and run offboarding
 -> bounded batched remediation
 -> new candidate when source/public surface/contract changes
 -> start a brand-new complete gate run with a new run identity
 -> repeat with no fixed pass ceiling until the latest complete run is clean
```

Targeted/affected reruns after a fix are regression evidence only; they never replace the required new complete gate run. Stop only the dependent unsafe/impossible lane; continue independent lanes. Do not patch the product in the middle of a frozen discovery pass.

## Product-quality closure and candidate freeze

The canonical ordering is:

```text
roadmap/function implementation complete enough for closure
 -> fast release preflight
 -> Surface Reconciliation convergence
 -> Full User E2E convergence
 -> rerun Surface Reconciliation if E2E remediation changed its surface contract
 -> Surface Reconciliation PASS + Full User E2E PASS on the same exact HEAD
 -> PRODUCT_QUALITY_CLOSURE=PASS
 -> freeze that exact HEAD as the release candidate
 -> release-specific upgrade/migration/platform/performance qualification
 -> final exact-HEAD machine CI/regression
 -> final artifacts + hashes/SBOM/provenance/manifest/attestation
 -> candidate/preview smoke when applicable
 -> owner acceptance / release authorization
 -> immutable tag/stable publication
 -> post-release public smoke
 -> monitor / rollback
```

A source, dependency, generated runtime artifact, public-surface, or applicable user-contract change after product-quality closure invalidates the affected user evidence and candidate freeze. Resume from the earliest invalidated user gate.

`PRODUCT_QUALITY_CLOSURE=PASS` is not release authorization. Machine qualification, integrity, approval, publication, and post-release smoke remain separate.

## Machine-readable evidence

Each gate emits one evidence JSON conforming to `schemas/user-acceptance-evidence.schema.json`.

Use:

```text
python3 tools/user_acceptance_contract.py validate-gate \
  --root <repository> \
  --evidence <gate.json>
python3 tools/user_acceptance_contract.py quality-close \
  --root <repository> \
  --surface-evidence <surface.json> \
  --e2e-evidence <e2e.json>
```

The validator independently resolves the repository's current Git HEAD, verifies the referenced contract bytes and digest at that exact commit, and rejects stale or dirty contract evidence. It is structural evidence only: evidence identity fields are self-reported metadata, so this tool does not establish terminal user-gate PASS, product-quality closure, or candidate-freeze eligibility and performs no merge, tag, publication, or deployment.

## Project-local contract requirements

Each user-facing repository must keep two repository-local contract documents referenced by `.engineering/release.yaml`.

Those contracts must translate this standard into the project's real public surface and supported capabilities. They define product-specific personas, capability/use-case inventory, mandatory scenarios, environment/topology constraints, cleanup rules, and evidence locations.

A project may be stricter than this standard. It may not weaken the mandatory ChatGPT executor, real public-surface, finding-accumulation, exact-candidate, ledger-derived evidence, or same-HEAD product-quality closure requirements.
