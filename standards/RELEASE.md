# Release Standard

## Principle

A release is qualified for an exact source revision and the artifacts produced from it. A PASS from another revision cannot be reused.

## Default lifecycle

candidate freeze -> deterministic regression -> build artifacts -> provenance / hashes / SBOM as required -> platform + lifecycle qualification -> performance / resilience -> operational E2E -> release decision -> immutable tag/release -> public smoke -> monitor / rollback if required

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

## Deprecation / EOL

When removing public behavior:
1. identify affected users/contracts
2. provide a deprecation path when practical
3. document replacement/migration
4. remove only at the approved release boundary
5. clean obsolete code/tests/docs after the compatibility decision is complete

Projects entering maintenance or retirement must define the security/support horizon and final migration/export path where applicable.

## Default blockers

- unresolved P0
- unresolved P1
- unresolved user-blocking P2
- required release gate failure
- provenance mismatch
- missing required security/platform/operational qualification
- known rollback/upgrade failure affecting supported paths

Build/qualification and publication are separate steps.