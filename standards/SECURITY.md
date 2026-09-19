# Security, Dependency & Supply-Chain Standard

Security is part of normal engineering work, not a release-only checklist.

## Secrets and credentials

- never commit secrets, tokens, private keys, passwords, or customer credentials
- prefer environment/secret stores and least privilege
- test that logs, diagnostics, bundles, errors, and generated commands do not leak secrets
- rotate/revoke exposed credentials immediately and treat exposure as an incident

## Dependency changes

Before adding or materially upgrading a dependency:
- confirm the dependency is actually needed
- prefer actively maintained, widely used, minimal dependencies
- record compatibility/runtime impact
- review license suitability
- run affected regression
- for production dependencies, consider known vulnerability exposure and rollback impact

Avoid dependency churn for cosmetic reasons.

## Open-source and license compliance

Projects that redistribute third-party software must track applicable:
- upstream project/version
- license and notice obligations
- source/binary redistribution obligations
- local modifications where required
- bundled/transitive components when material

Do not remove required upstream notices.

## Supply-chain integrity

Release-oriented projects should use, as applicable:
- immutable source revision
- artifact hashes
- release manifest
- SBOM
- provenance/attestation
- pinned reusable workflow/action revisions where practical

## Security bug workflow

```text
contain -> assess scope -> reproduce safely -> regression -> fix -> affected security tests -> wider qualification
```

Do not publish sensitive exploit details before the remediation/release plan is ready.

## Input and trust boundaries

Treat external input, configuration, API payloads, files, network data, plugin/tool output, and AI-generated commands as untrusted until validated.

Security-sensitive defaults should fail closed unless the product contract explicitly requires otherwise.
