# Knowledge, Documentation & Source-of-Truth Standard

Engineering knowledge must be durable enough that a future human or AI agent can reconstruct why the system behaves as it does.

## Source-of-truth hierarchy

Default:
1. repository code/config/schema for executable truth
2. repository engineering metadata and canonical product specifications
3. ADRs for durable decisions
4. runbooks/RCA for operations and incidents
5. Wiki for human-readable and AI-searchable derived knowledge

If a Wiki page conflicts with canonical Git content, Git wins unless the project explicitly defines another authority.

## Documentation rules

Update documentation when a change alters:
- public API/CLI
- install/upgrade/uninstall behavior
- configuration or persisted data
- security model
- operational procedures
- supported platforms
- release/rollback procedure

Avoid duplicating the same normative rule in many locations. Link to canonical content.

## ADRs

Use ADRs only for durable, expensive-to-reverse decisions such as architecture boundaries, public contracts, security model, persistent formats, or major runtime dependencies.

## Product/specification master

Projects with a Product Master or equivalent must identify its canonical path/version. Implementation must not silently diverge from accepted product semantics.

## Wiki

Wiki is for navigation, explanation, search, and cross-project context. Durable normative changes happen in the canonical repository first, then sync to Wiki.

## Incident and field knowledge

Meaningful production/manual findings should become one or more of:
- regression scenario
- test invariant
- runbook improvement
- RCA
- ADR
- product requirement clarification

Knowledge that only exists in a chat is not considered durable engineering state.
