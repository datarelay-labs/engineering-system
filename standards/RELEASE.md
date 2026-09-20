# Release Standard

## Principle

A release is qualified for an exact source revision and the artifacts produced from it. A PASS from another revision cannot be reused.

## Default lifecycle

```text
candidate freeze
 -> fast release preflight
 -> full deterministic qualification
 -> build artifacts
 -> provenance / hashes / SBOM as required
 -> platform + lifecycle qualification
 -> performance / resilience
 -> operational E2E
 -> release decision
 -> immutable tag/release
 -> public smoke
 -> monitor / rollback if required
```

A blocking failure stops downstream expensive stages. Fix the blocker, create the new exact candidate if source changes, then restart the required qualification from the appropriate boundary.

## Fast release preflight

Before a long full suite, run a short deterministic command containing the highest-value blockers: syntax/static validation, known critical regressions, release-governance checks, and other project-specific fast checks.

The preflight should take minutes rather than hours. It is not a substitute for required full qualification; its purpose is to avoid wasting time and compute on a candidate that is already known to fail.

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
