# Engineering System Enforcement

The Engineering System is useful only if agents and CI actually consume it.

## Applicability

The global default applies to every software/product engineering project the owner works on with ChatGPT or Cursor, regardless of GitHub organization, repository owner, product name, or project location. This includes `datarelay-labs`, `xdr-labs`, and future related repositories.

The canonical standard lives in `datarelay-labs/engineering-system`.

## Enforcement layers

### 1. Global AI bootstrap

ChatGPT Project/Custom Instructions and Cursor User Rules establish the default rule before any repository-specific files are read.

If the target repository has not yet adopted the Engineering System, the agent must identify the adoption gap and still follow the canonical standard instead of silently ignoring it.

### 2. Repository entrypoint

Every adopted repository must contain:
- `AGENTS.md`
- `.engineering/project.yaml`
- `.engineering/tests.yaml`
- `.engineering/release.yaml`

An AI agent must read these before development, debugging, testing, release, upgrade, operations, incident, or documentation work.

### 3. Cursor persistent rule

Every adopted repository must contain `.cursor/rules/engineering-system.mdc` with `alwaysApply: true`.

### 4. Deterministic CI compliance

Adopted repositories should call the shared `adoption-compliance.yml` workflow. It validates mandatory context and persistent Cursor rule presence.

Protected integration/release branches should require the applicable Engineering System checks after project adoption is proven.

### 5. Work evidence

A task is not complete merely because an agent states that it followed the standard. Evidence is the applicable combination of affected tests, regression evidence, security/compatibility validation, lifecycle/platform validation, release qualification, and exact source/artifact identity.

## Fail-closed behavior

Missing or contradictory mandatory engineering context is a configuration defect. Do not silently continue as if repository-specific compliance had been established.

## Enforcement chain

global/project AI instruction -> repository AGENTS/rules -> .engineering metadata -> pinned Engineering System -> deterministic GitHub gates
