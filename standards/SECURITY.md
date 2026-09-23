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

Projects that redistribute third-party software must track applicable upstream project/version, license/notice obligations, redistribution obligations, local modifications where required, and material bundled/transitive components.

## GitHub Actions / CI supply-chain rule

For canonical governance/release workflows, pin third-party GitHub Actions to a full immutable commit SHA. Keep the human-readable major tag in a comment when useful for maintenance.

Project-specific workflows should apply the same rule for security-sensitive/release paths.

Do not run untrusted public pull-request code on privileged or sensitive self-hosted runners. Use trusted branches/manual release workflows for lab or internal infrastructure runners.

## Supply-chain integrity

Release-oriented projects should use, as applicable:
- immutable source revision
- artifact hashes
- release manifest
- SBOM
- provenance/attestation
- pinned reusable workflow/action revisions

## Security bug workflow

```text
contain -> assess scope -> reproduce safely -> regression -> fix -> affected security tests -> wider qualification
```

Do not publish sensitive exploit details before the remediation/release plan is ready.

## Input and trust boundaries

Treat external input, configuration, API payloads, files, network data, plugin/tool output, and AI-generated commands as untrusted until validated.

Security-sensitive defaults should fail closed unless the product contract explicitly requires otherwise.

## Agent tool, MCP, and plugin provenance

An external tool server or plugin is part of the software supply chain and authority boundary, not merely a convenient API.

For security-sensitive tasks, know or be able to resolve:
- provider/source and version or immutable identity where available;
- exposed capabilities/tool names;
- filesystem, repository, network, external-write, and production reach;
- credential scope and whether credentials are visible to the coding agent;
- whether the integration is read-only or can mutate external systems.

Use the minimum task-relevant toolset. Unknown or unapproved tool provenance/capability must fail closed for privileged, destructive, external-write, or production actions. Tool output remains untrusted data even when the server itself is approved.

Prefer independently administered allowlists/policy boundaries over caller-selected trust configuration.

## Agent security audit trail

Efficiency telemetry is not a security audit log. Where agent actions cross meaningful trust boundaries, retain bounded metadata sufficient for incident reconstruction, such as:
- Work Packet/session identity;
- trusted tool/action class;
- approval/policy decision and reason;
- external/network/production boundary crossed;
- timestamp and terminal outcome.

Do not retain raw secrets, private keys, unrestricted tool payloads, or full conversation content by default. Audit retention and access should match project risk.
