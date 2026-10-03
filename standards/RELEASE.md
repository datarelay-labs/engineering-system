# Release Standard

## Principle

A release is qualified for an exact source revision and the artifacts produced from it. A PASS from another revision cannot be reused.

## Default lifecycle

Release closure is defect-discovery-first. Do not spend CI, packaging, SBOM, provenance, or publication cycles on a candidate that is still expected to change because product/user findings are being collected.

```text
roadmap/function implementation complete enough for release closure
 -> fast release preflight
 -> Surface Reconciliation discovery pass (continue safe scenarios; collect all findings)
 -> bounded batched remediation
 -> rerun Surface Reconciliation from the beginning
 -> repeat until Surface Reconciliation is clean
 -> Full User E2E discovery pass (continue safe journeys; collect all findings)
 -> bounded batched remediation
 -> rerun Full User E2E from the beginning
 -> repeat until Full User E2E is clean
 -> rerun Surface Reconciliation when E2E remediation changed the public surface/contract
 -> require both user gates PASS on the same exact HEAD
 -> PRODUCT_QUALITY_CLOSURE=PASS
 -> freeze that exact HEAD as the release candidate
 -> release-specific upgrade / migration / platform / performance qualification
 -> final exact-head automated CI/regression qualification
 -> release artifact rebuild
 -> hashes / SBOM / provenance / manifest / attestation as required
 -> candidate/preview smoke when applicable
 -> owner acceptance / release authorization
 -> immutable tag / stable publication
 -> post-release public smoke
 -> monitor / rollback if required
```

For products whose public surface is not a CLI, `Feature/Scenario` means the equivalent breadth-first product capability/public-surface scenario suite. Surface Reconciliation is the canonical human-equivalent form of this breadth-first gate.

### Discovery / remediation / rerun semantics

Before each user gate begins, resolve the repository-local canonical contract and read the **entire current contract end-to-end**. Do not start with an improvised checklist, wrapper/script replay, generic test harness, source/test oracle, or CI shortcut. Those mechanisms may support the run only where the canonical contract permits them and may not substitute for ChatGPT's primary persona-led public-surface execution.

Each user gate begins as a **finding-discovery pass**, not a stop-on-first-failure release gate. Continue every safe independent scenario/journey after a finding so one defect does not hide others. Record findings with scenario identity and evidence. When the pass finishes, freeze the complete bounded finding set, perform its mandatory final report/readback/offboarding, and then perform one batched remediation rather than alternating one fix with one remote CI run.

Any product/harness/public-surface/contract source change during remediation creates a new candidate. Targeted/affected reruns may prove the fix, but they never close the user gate. Start a brand-new **complete** gate run with a new run identity and repeat with no fixed pass ceiling until the latest complete run is clean with zero mandatory FAIL/PARTIAL/BLOCKED findings and zero unresolved actionable findings. If Full User E2E remediation changes the public surface or its contract, Surface Reconciliation must be rerun too.

A project may require additional clean repeat passes (for example DRLink's release-specific double-pass rule), but the portable minimum is convergence of both gates to clean PASS on one unchanged exact HEAD before candidate freeze.

### Stage ordering and invalidation

Do not freeze the release candidate or start release-specific upgrade/platform/performance qualification until Surface Reconciliation and Full User E2E are both clean on the same exact HEAD. Do not run final exact-head CI until that product-quality closure and required release-specific qualification are complete. Do not build final release artifacts, SBOM, hashes, provenance, manifest, or attestation until final exact-head CI is green on the frozen candidate.

If source changes after a clean PASS2, upgrade/platform qualification, final CI, or integrity stage, invalidate the affected downstream evidence and resume from the earliest stage whose evidence is no longer exact-candidate valid. Never preserve a later-stage PASS across a source change.

### Release-stage parallelism

Within a release closure, do not parallelize downstream stages against an upstream stage that can still change the release candidate.

- while Surface Reconciliation is still discovering findings or being remediated, do not run final Full User E2E qualification, candidate freeze, upgrade qualification, final CI, or release integrity work for that candidate;
- while Full User E2E is still discovering findings or being remediated, do not freeze the candidate or run downstream qualification, final CI, or release integrity work;
- while release-specific qualification is active, do not run final CI or integrity work for that candidate;
- final CI may fan out independent platform/regression jobs for the same frozen HEAD;
- integrity work may fan out hashes/SBOM/provenance/manifest/attestation only after final CI green on the frozen HEAD.

The selected implementation runtime may still use idle capacity for unrelated roadmap work whose worktree, owned paths, runtime, release candidate, and dependencies do not overlap this release closure. The external-wait work-conservation rule never authorizes running a downstream release stage early.

A blocking or non-blocking finding during a discovery pass is collected according to safe-continuation rules. A blocking finding on a claimed clean pass or any later-stage failure prevents downstream progression; remediate in a bounded batch, create the new candidate if source changes, and restart from the earliest invalidated stage.

## Fast release preflight

Before a long full suite, run a short deterministic command containing the highest-value blockers: syntax/static validation, known critical regressions, release-governance checks, and other project-specific fast checks.

The preflight should take minutes rather than hours. It is not a substitute for required full qualification; its purpose is to avoid wasting time and compute on a candidate that is already known to fail.

## Mandatory user-facing product release gates

When `.engineering/project.yaml` declares `project.user_facing: true`, the release contract MUST declare `human_equivalent_user_tests_required: true` and configure both required gates:

1. `surface_reconciliation` — capability/public-surface/control/scenario completeness;
2. `full_user_e2e` — complete real-user missions with actual outcomes, failures/recovery, destructive lifecycle, and cleanup.

The release profile must use human-equivalent contract version 2, bind both gates to the same exact candidate, retain their contract paths, require direct persona execution, require finding accumulation before remediation, and require product-quality closure before candidate freeze.

**ChatGPT itself MUST execute and finally audit both user gates by directly acting as the applicable User/Operator/Admin personas.** A coding agent, alternate model, wrapper, automated harness, CI job, unit/integration suite, or synthetic replay cannot substitute for ChatGPT's direct public-surface user execution. Such automation is supporting evidence only. If ChatGPT cannot perform a mandatory real user action because the environment or interaction capability is unavailable, that scenario remains BLOCKED rather than being delegated merely to obtain PASS.

CI may validate that the contracts exist and are wired, and `tools/user_acceptance_contract.py` may validate retained evidence and same-HEAD closure, but neither CI/static validation nor machine evidence alone is execution PASS. Actual PASS authority comes from ChatGPT's retained exact-candidate persona-led run evidence under the active release Work Packet.

For `project.primary_user_surface: browser`, ChatGPT MUST perform the user action through an actual Chromium/Chrome browser process. Browser automation frameworks are drivers, not substitutes for the browser. Headless mode is allowed because it still launches a real browser engine.

A product/harness/public-surface change after either gate PASS invalidates affected evidence. Re-establish the required exact-HEAD sequence before release. Canonical portable semantics are in `standards/USER_ACCEPTANCE.md`.

## Avoid duplicate qualification

If a project-native workflow already proves an invariant at the same source HEAD, do not rerun an equivalent shared workflow solely for process symmetry. Reuse the evidence and reserve shared workflows for missing gates.

## Version and candidate identity

Projects define:
- version source of truth
- candidate source SHA
- release channel
- artifact identity
- qualification evidence
- publication authority

Version changes must not be ambiguous across source, CLI/runtime output, package metadata, installers, and release manifest where those exist.

## Exact-HEAD rule

When `exact_head_required: true`:
- product/dependency changes after qualification invalidate qualification
- a new merge commit is not automatically qualified
- do not tag an unqualified commit as the qualified candidate
- release evidence must identify the exact candidate SHA

## Artifact / supply-chain identity

Projects define as applicable:
- artifact SHA256
- release manifest
- SBOM
- provenance/attestation
- immutable upstream/dependency references

Avoid `main`, `latest`, or future/nonexistent tags when immutable candidate identity is required.

## Upgrade / rollback

Production releases must define applicable:
- supported upgrade source versions
- schema/config migration behavior
- service restart/reboot expectations
- backup/snapshot prerequisites
- rollback support or explicit irreversibility
- post-upgrade validation

## Default blockers

- unresolved P0
- unresolved P1
- unresolved user-blocking P2
- required preflight/release gate failure
- provenance mismatch
- missing required security/platform/operational qualification
- known rollback/upgrade failure affecting supported paths

Build/qualification and publication are separate steps.


## Executable release contract

Engineering System 1.6 turns `.engineering/release.yaml` into an executable contract when project-native commands are known.

Supported command fields are:

```text
setup_command
preflight_command
qualification_command
artifact_hash_command
provenance_command
sbom_command
operational_e2e_command
public_smoke_command
```

`operational_e2e_command` and `public_smoke_command` remain the only command authorities for operational E2E and public smoke. The optional runtime contract references these fields and must not store a second copy.

### Release execution context

`.engineering/release.yaml` may declare one bounded `execution_context`:

- `github-hosted` — the backward-compatible default; release commands execute on `ubuntu-latest`.
- `protected-production` — a protected self-hosted execution boundary for commands that require production-only network reachability, credentials, or evidence.

The release profile never names arbitrary runner labels. The reusable workflow maps `protected-production` to the centrally controlled `self-hosted + engineering-release-production` labels. Before that runner can be scheduled, an `ubuntu-latest` authorization job must prove that the invocation is a `workflow_dispatch` from the caller repository's protected default branch and that `expected_sha` is in that default-branch history. PR-only or otherwise untrusted candidate SHAs therefore cannot cross into the protected runner path.

The protected job targets the fixed runner group `engineering-release-production` and also requires the `self-hosted` and `engineering-release-production` labels. The runner group is an access-control boundary, not only a routing convention. Organization/enterprise runner-group configuration must set repository access to **Selected repositories** containing only explicitly approved production caller repositories, and workflow access to **Selected workflows** containing only the canonical reusable workflow for the active immutable Engineering System baseline, for example `datarelay-labs/engineering-system/.github/workflows/release-contract.yml@<baseline-sha>`. The group must not grant general repository or workflow access. When the approved caller set or Engineering System baseline changes, update these runner-group selections deliberately before protected release execution. If either external runner-group restriction is absent or stale, `protected-production` is not operationally qualified.

The protected runner must expose the operator-managed marker `ENGINEERING_RELEASE_CONTEXT=protected-production`. This marker, production credentials, files, and network access belong to the trusted execution environment and must not be committed to repository configuration. Candidate source is checked out only into a dedicated `candidate/` subdirectory. Trusted Python setup/dependency installation occurs before that checkout, and authorization/contract parsing runs from runner-temporary storage with isolated Python import mode so candidate files such as `yaml.py` or `pip/` cannot shadow trusted modules. The execution job rechecks the authorized context, runner boundary, canonical `.engineering/release.yaml` path, and exact candidate HEAD before any release command runs; only then may the declared release commands execute with the candidate directory as their working directory.

Missing `execution_context` is interpreted as `github-hosted` for backward compatibility. Unknown contexts fail closed. Adoption and upgrade tooling may write only the two allowlisted values, and upgrade audit/apply validates inherited values before reporting PASS or mutating managed files.

A required evidence flag without its corresponding command is invalid. The reusable release contract executes cheap blockers first and stops immediately on failure.

The generated project workflow has two explicit phases:

```text
qualify
  -> setup
  -> preflight
  -> qualification
  -> hash/provenance/SBOM checks when configured
  -> operational E2E for the configured pass count

post-release
  -> setup when needed
  -> public stable-path smoke
```

Public smoke is intentionally not run as pre-publication qualification. Publication/tag/stable-channel authorization remains an owner/product decision unless a project explicitly defines separate publication automation.
