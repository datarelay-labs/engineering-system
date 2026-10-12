# User Acceptance Standard

## Purpose

Every user-facing product requires two distinct human-equivalent quality gates before a release candidate may be frozen:

1. **Surface Reconciliation** — breadth-first proof that supported capabilities map coherently to the actual public user surface.
2. **Full User E2E** — depth-first proof that realistic users can complete supported missions and observe the intended real outcomes.

Project-specific scenario catalogs stay in the project repository. This standard defines portable execution semantics; it does not prescribe product-specific commands, pages, hosts, or topology.

## Mandatory executor

**The default `AI_DIRECT` mode is unchanged:** the current execution-profile runtime is the acting user executor and final auditor of both gates. The selected runtime directly acts as the applicable End User, Operator, Administrator, Incident Responder, or Platform Maintainer on the supported public surface.

A different AI runtime, wrapper, shell script, browser test script, unit/integration suite, CI job, or automated harness MUST NOT impersonate the acting user or declare either gate PASS. Browser drivers can support real actions but cannot substitute scenario judgments.

In `AI_DIRECT`, the selected runtime owns persona goals, public-surface interactions, observations, recovery, scenario disposition and final judgment. Its default contract remains:

```text
USER_ACCEPTANCE_EXECUTOR=EXECUTION_PROFILE
USER_ACCEPTANCE_FINAL_AUDITOR=EXECUTION_PROFILE
DIRECT_PERSONA_EXECUTION=REQUIRED
ALTERNATE_RUNTIME_USER_EXECUTION=FORBIDDEN
SCRIPTED_USER_SCENARIO_EXECUTION=FORBIDDEN
AUTOMATED_HARNESS_ROLE=SUPPLEMENTAL_ONLY
```

If the selected runtime cannot perform a mandatory real user action, the scenario is BLOCKED under `AI_DIRECT`. An alternate AI runtime must not be silently substituted.

### Explicit human-owner opt-in — acting user is human, auditor remains ChatGPT

When the **currently authenticated owner explicitly opts in** for a particular candidate and user gate, `HUMAN_OWNER_WITH_CHATGPT_AUDIT` is a permissible alternative *user-action* mode. This is not an execution-profile runtime switch and does not use or bypass the unmerged owner-runtime-override signer (#297/#301). The selected ChatGPT runtime remains the **final independent auditor** of the real user evidence. The human owner personally interacts with the real Chrome/Chromium browser or supported primary CLI/desktop/mobile surface; another agent, hidden wrapper, simulated DOM, API-only test, or scripted scenario cannot count as that human's action.

Only a genuine explicit owner instruction in the live, authenticated conversation (and independently verifiable owner permission when the host provides it) may choose this mode. An Issue comment, screenshot, boolean, model text, purported `owner_authenticated` flag, or self-supplied signing key **cannot authenticate a human**. When user identity or execution provenance cannot be established, mark the human run **BLOCKED / UNVERIFIED**, not PASS. A report reference or authenticated GitHub write proves the referenced artifact's origin at most; it does not prove who physically operated the browser.

For an explicitly selected human-user gate:

1. ChatGPT first reads the **entire canonical gate contract** and confirms candidate/repository, exact HEAD, committed contract bytes/hash, roles, scenario inventory, real surface URL/localhost preview, prohibited actions and cleanup bounds. It provides the bounded user mission without leaking a source-level answer key.
2. The owner physically performs the actual public-surface actions and reports per-scenario timestamps, action intent, expected/observed UI outcomes, any failures and recovery, live screenshot/video and/or independent persisted readback, browser/process/version or actual primary-surface metadata, and run-owned state/cleanup. Evidence must be bound to the **same immutable candidate**, contract digest, run ID and the authenticated owner's opt-in. No secrets or production tokens are required in the ledger.
3. ChatGPT independently checks the observation against the entire scenario catalog, authentic owner opt-in, browser/public-surface evidence, real effects, cross-session consistency, role/access controls, negative paths, remediation and cleanup. An incomplete, fabricated, replayed, script-only, stale, cross-HEAD or untrusted report is **BLOCKED**. The owner's `PASS` claim is input to audit, never the audit decision.
4. Full breadth and defect-convergence are unchanged: **100% applicable mandatory missions**, zero unresolved FAIL/PARTIAL/BLOCKED/finding, targeted regression then a fresh complete confirmation on the same exact HEAD. Any source, scenario or contract mutation invalidates affected evidence and requires appropriate re-execution.
5. ChatGPT records its **own** evidence audit separately from the owner's observation, with an auditable reference and actual findings. Even a successful independent user-gate audit is **not** GitHub merge, production, candidate-freeze, release, credential or deployment authorization; existing owner/security/release gates still apply.

The product's managed release-profile `human_equivalent_user_tests.executor` remains `EXECUTION_PROFILE` in both modes because it selects the **ChatGPT audit authority**, not the physical human action mode. No managed adopter silently switches a release profile or an execution runtime. The optional acting-user mode belongs to the **one exact run's evidence** after the owner's explicit opt-in, and it is reviewed by the product's own Chat before user-quality closure.

The structural JSON contract accepts optional `user_execution_mode=HUMAN_OWNER_WITH_CHATGPT_AUDIT`, `executor=HUMAN_OWNER`, `runtime` equal to the committed ChatGPT execution profile, `final_auditor=EXECUTION_PROFILE` and `human_owner_evidence` with owner-intent/observation/audit references, exact candidate/run/contract identity, real process/surface/version and scenario timestamps/results and screenshot/readback references. In this mode the structural validator resolves the **actual Git origin of the candidate worktree** and requires the owner instruction, observation, process, scenario, readback and ChatGPT audit evidence references to belong to **that same repository**. A self-consistent set of links to an unrelated GitHub repository must BLOCK, even when each URL is syntactically valid. A missing or non-GitHub origin also BLOCKS the human mode; ordinary `AI_DIRECT` behavior is unchanged. Missing `user_execution_mode` means `AI_DIRECT`; its existing evidence remains compatible. A human owner's purported `chatgpt_audit_claim=PASS` inside JSON is **not authenticated** merely because it passes schema checks.

`tools/user_acceptance_contract.py validate-gate` and `quality-close` perform structural checks only. They emit `HUMAN_OWNER_ATTESTATION=UNVERIFIED`, `CHATGPT_AUDIT_PROVENANCE=UNVERIFIED`, `USER_GATE_EXECUTION_PASS=NOT_ESTABLISHED`, `PRODUCT_QUALITY_CLOSURE=BLOCK`, `CANDIDATE_FREEZE_ELIGIBLE=NO` and `AUTHORIZES_RELEASE=NO` without a separate trusted audit. This limitation already applies to `AI_DIRECT`: caller-authored metadata never mints real-world operator provenance. A genuinely signed owner operator receipt would need a separate trusted issuer and explicit security/permission review, not repo-writable or test keys.

## Applicability

This standard applies when `.engineering/project.yaml` declares `project.user_facing: true`.

The release profile must use `human_equivalent_user_tests.contract_version: 2`, reference repository-local contracts for both gates, require the same candidate, and require user-quality convergence before candidate freeze.

Machine/unit/component/API/static checks may support the gates but never substitute for the actual user action.

## Contract-first execution hard gate

Before **either** user gate starts, the selected runtime MUST resolve the repository-local canonical contract referenced by the release profile and read the **entire current contract end-to-end**. This happens before scenario execution, wrapper/harness execution, source/parser inspection, CI inspection, or an improvised checklist. The purpose is to bind execution to the project's actual personas, scope, prohibited actions, evidence rules, cleanup, invalidation, and PASS/FAIL/BLOCKED semantics rather than to the executor's remembered or guessed version of the test.

A run is invalid as user-gate evidence when it starts from a wrapper/scripted replay, generic test harness, source/test oracle, or CI shortcut before the canonical contract has been fully read and adopted for that run. Automation remains supporting evidence/orchestration only; it never becomes the acting persona merely because it covers many commands or scenarios.

A standalone request for one gate authorizes only that gate. Chaining Surface Reconciliation into state-changing Full User E2E requires either the current release-quality workflow/Work Packet to require the next gate or an explicit Full User E2E/release-qualification request.

Managed adoption/upgrade MUST NOT infer these contract semantics merely from the existence of contract paths or from pre-existing boolean declarations. Writing compliance declarations for contract-first execution, targeted affected convergence followed by complete confirmation rerun, or wrapper non-substitution requires an explicit review acknowledgement made only after both repository-local gate contracts have been inspected and updated as needed. `complete_rerun_after_remediation_required=true` means **after the remediation batch is targeted-clean**, not after every individual fix. The managed release profile records `contract_review_attestation_version: 1`; absence of that attestation means reviewed provenance is unknown and upgrades fail closed until review is acknowledged.

## Shared execution semantics

Both gates MUST bind to an exact candidate HEAD/build and exact committed test contract, use the actual supported primary public surface, require direct persona actions by either the selected runtime in `AI_DIRECT` or the explicitly opted-in human owner with independent ChatGPT audit, and separate acting-user knowledge from auditor/source/test oracle knowledge. Both gates MUST also:

- continue every safe independent scenario after a finding;
- freeze the complete bounded finding set before remediation;
- remediate findings in a bounded batch instead of alternating each finding with expensive release qualification;
- restart invalidated coverage after source/public-surface/contract changes;
- retain machine-readable scenario/finding ledgers and derive summaries from those ledgers;
- record blocked/not-applicable work honestly rather than converting it to PASS;
- require zero mandatory FAIL/PARTIAL/BLOCKED results and zero unresolved blocking findings for release PASS;
- preserve evidence and clean up only run-owned state/processes.

Parallel execution is preferred whenever independent lanes are isolated. It must never corrupt shared product state or evidence.

For browser products, the acting user (`AI_DIRECT` runtime or opted-in human owner) must operate a real Chromium/Chrome process. Under `AI_DIRECT`, Playwright or another browser driver may support the selected runtime's real actions. Under human-owner mode, browser automation never substitutes for the owner's own physical action. Component rendering, jsdom, API-only checks, screenshots without interaction, and static DOM checks are supporting evidence only.

For CLI/desktop/mobile/mixed/other surfaces, the acting user uses the corresponding actual supported public interface with the same mode boundary.

## Gate A — Surface Reconciliation

Surface Reconciliation is **feature-first and black-box-first**.

Start from the supported product capability inventory, not from the implementation's command/control list. For each supported capability reconcile product capability, public surface/control, discovery path, role/context, user goal, lifecycle/scenario, error/empty/recovery guidance, result, and evidence.The acting persona discovers the product through the public surface first. Source, parser, route, component, generated metadata, internal catalog, test code, and scenario oracle knowledge may be used only as post-hoc auditor evidence after the corresponding public-surface evidence is frozen.

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

For each applicable supported mission, the selected runtime (`AI_DIRECT`) or the explicitly opted-in human owner assumes the applicable user role and maps:

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
 -> affected scenario/journey reruns until every frozen finding is targeted-clean
 -> start one brand-new complete confirmation run with a new run identity
 -> if new findings appear, return to affected convergence and then run one new complete confirmation
 -> repeat with no fixed pass ceiling until the latest complete run is clean
```

Targeted/affected reruns after a fix are required regression evidence for the changed slice, but they never replace the final complete confirmation run. Conversely, the complete gate MUST NOT be restarted after every individual fix when untouched scenarios have no affected/invalidation relationship. Stop only the dependent unsafe/impossible lane; continue independent lanes. Do not patch the product in the middle of a frozen discovery pass.

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

A project may be stricter than this standard. It may not weaken the mandatory execution-profile-selected executor, real public-surface, finding-accumulation, exact-candidate, ledger-derived evidence, or same-HEAD product-quality closure requirements.
