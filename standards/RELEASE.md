# Release Standard

## Principle

A release is qualified for an exact source revision and the artifacts produced from it. A PASS from another revision cannot be reused.

## Default lifecycle

Release closure is defect-discovery-first. Do not spend CI, packaging, SBOM, provenance, or publication cycles on a candidate that is still expected to change because product/user findings are being collected.

```text
development complete enough for release closure
 -> product Feature/Scenario PASS1 (continue safe scenarios; collect all findings)
 -> one batched remediation of PASS1 findings
 -> new candidate
 -> product Feature/Scenario PASS2 (zero mandatory findings)
 -> Full User E2E PASS1 (continue safe journeys; collect all findings)
 -> one batched remediation of E2E PASS1 findings
 -> new candidate
 -> Full User E2E PASS2 (zero mandatory findings)
 -> release-specific upgrade / migration / platform qualification
 -> final exact-head automated CI/regression qualification
 -> release artifact rebuild
 -> hashes / SBOM / provenance / manifest / attestation as required
 -> final release audit: every mandatory evidence class binds to the same exact candidate
 -> merge/tag/stable publication authorization
 -> immutable tag/release
 -> public smoke
 -> monitor / rollback if required
```

For products whose public surface is not a CLI, `Feature/Scenario` means the equivalent breadth-first product capability/public-surface scenario suite. Surface Reconciliation is the canonical human-equivalent form of this breadth-first gate.

### PASS1 / remediation / PASS2 semantics

PASS1 is a **finding-discovery pass**, not a stop-on-first-failure release gate. Continue every safe independent scenario/journey after a finding so one defect does not hide others. Record findings with scenario identity and evidence. When PASS1 finishes, classify the complete bounded finding set and perform one batched remediation rather than alternating one fix with one remote CI run.

Any product/harness/public-surface source change during remediation creates a new candidate. PASS2 restarts the same suite from the beginning on that new candidate. PASS2 requires zero mandatory FAIL/PARTIAL/BLOCKED findings.

The same rule applies independently to Full User E2E: E2E PASS1 exhausts safe real-user journeys and collects findings; remediation is batched; E2E PASS2 restarts from the beginning and must be clean.

### Stage ordering and invalidation

Do not start release-specific upgrade/platform qualification until product Feature/Scenario PASS2 and Full User E2E PASS2 are clean for the candidate. Do not run final exact-head CI until those semantic/user gates and required release-specific qualification are complete. Do not build final release artifacts, SBOM, hashes, provenance, manifest, or attestation until final exact-head CI is green and product code is frozen.

If source changes after a clean PASS2, upgrade/platform qualification, final CI, or integrity stage, invalidate the affected downstream evidence and resume from the earliest stage whose evidence is no longer exact-candidate valid. Never preserve a later-stage PASS across a source change.

### Release-stage parallelism

Within a release closure, do not parallelize downstream stages against an upstream stage that can still change the release candidate.

- while Feature/Scenario PASS1/PASS2 is active, do not run Full User E2E qualification, upgrade qualification, final CI, or release integrity work for that candidate;
- while Full User E2E PASS1/PASS2 is active, do not run upgrade qualification, final CI, or release integrity work for that candidate;
- while release-specific qualification is active, do not run final CI or integrity work for that candidate;
- final CI may fan out independent platform/regression jobs for the same frozen HEAD;
- integrity work may fan out hashes/SBOM/provenance/manifest/attestation only after final CI green on the frozen HEAD.

The selected implementation runtime may still use idle capacity for unrelated roadmap work whose worktree, owned paths, runtime, release candidate, and dependencies do not overlap this release closure. The external-wait work-conservation rule never authorizes running a downstream release stage early.

A blocking or non-blocking finding during PASS1 is collected according to safe-continuation rules. A blocking PASS2 or later-stage failure prevents downstream progression; remediate in a bounded batch, create the new candidate if source changes, and restart from the earliest invalidated stage.

## Fast release preflight

Before a long full suite, run a short deterministic command containing the highest-value blockers: syntax/static validation, known critical regressions, release-governance checks, and other project-specific fast checks.

The preflight should take minutes rather than hours. It is not a substitute for required full qualification; its purpose is to avoid wasting time and compute on a candidate that is already known to fail.

## Mandatory user-facing product release gates

When `.engineering/project.yaml` declares `project.user_facing: true`, the release contract MUST declare `human_equivalent_user_tests_required: true` and configure both required gates:

1. `surface_reconciliation` — capability/public-surface/control/scenario completeness;
2. `full_user_e2e` — complete real-user missions with actual outcomes, failures/recovery, destructive lifecycle, and cleanup.

The release profile must bind both gates to the same exact candidate and retain their contract paths. CI may validate that the contracts exist and are wired, but CI/static validation alone MUST NOT be interpreted as execution PASS. Actual PASS authority comes from the active release Work Packet plus retained exact-candidate run evidence.

For `project.primary_user_surface: browser`, the release profile MUST require an actual Chromium/Chrome browser process. Browser automation frameworks are drivers, not substitutes for the browser. Headless mode is allowed because it still launches a real browser engine.

A product/harness/public-surface change after either gate PASS invalidates affected evidence. Re-establish the required exact-HEAD sequence before release.

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
