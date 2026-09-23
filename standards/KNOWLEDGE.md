# Knowledge, Documentation & Source-of-Truth Standard

Engineering knowledge must be durable enough that a future human or AI agent can reconstruct current intended behavior without replaying old chat history.

## Final-state knowledge model

Do not preserve intermediate tool evaluations or implementation detours in the public playbook unless they remain operationally relevant. Share the final adopted method, contract, and usage.

## Canonical roles

### Product Master — optional
Use only for complex products that need a durable product-level model: concepts, long-lived invariants, supported boundaries, and product semantics. Do not duplicate implementation detail already canonical elsewhere.

### OpenSpec or equivalent specification system
When adopted by a project:
- current accepted behavior lives in the project's canonical specs
- active accepted changes live in change proposals/work items
- completed changes may be archived after implementation and evidence are complete

A specification system is optional; do not add a second competing specification framework to a project that already has an adequate one.

### Decision Event
A Decision Event is a structured handoff for an explicitly accepted decision. It is not an additional permanent source of truth. Uncertain discussion text must not be auto-promoted into an accepted decision.

### AI Work Packet
An AI Work Packet is repository-scoped current coordination state for resuming an active workstream across AI sessions/tools. It is not a normative product specification and must not duplicate canonical documents or accumulate conversation history. Use `standards/SESSION_CONTINUITY.md` for the contract.

### ADR
Use only for durable, expensive-to-reverse architecture/security/persistence/public-contract decisions.

### Code + tests
Executable implementation and deterministic behavior evidence.

### Runbook / RCA
Operational procedure and incident learning.

### Wiki / Athena
Human-readable navigation, explanation, cross-project context, and AI search. It is a derived knowledge layer, not a competing normative source.

## Default authority

1. executable code/config/schema for runtime truth
2. canonical product/specification artifacts for intended behavior
3. repository engineering metadata
4. ADRs for durable architectural decisions
5. runbooks/RCA for operations/incidents
6. Wiki/Athena for derived explanation/search

If derived knowledge conflicts with canonical Git content, canonical Git content wins.

## Context-efficiency rule

Agents should retrieve only the knowledge needed for the current task. Do not load all Wiki pages, all archived specifications, all ADRs, or historical discussions into every task context.

## Optional knowledge index

A repository may keep a small machine-readable domain map at `.engineering/knowledge.yaml`. The file is optional. Repositories without it remain valid, and adoption does not create or rewrite one. Bootstrap and managed upgrade install `tools/knowledge-contract.py` and `schemas/knowledge-index.schema.json` when those paths are missing. A pre-existing different copy fails closed before any adoption or upgrade writes.

The index routes each domain to canonical Git paths. It does not copy normative text. Schema: `schemas/knowledge-index.schema.json`.

`python3 tools/knowledge-contract.py check` validates a present index. It reports missing canonical or derived paths, source-of-truth conflicts where one path is both canonical and derived or generated, and stale generated references whose recorded `source_sha256` does not match the source file. Default output is bounded. A truncated report prints `FULLER` and `RAW` commands that reproduce the complete finding list.

## Retrieval escalation

Local search remains the default. `python3 tools/knowledge-contract.py route` reads only a local signals file and does not call a retrieval vendor, vector database, or network API. No signals means `RETRIEVAL=LOCAL`.

Escalation requires recorded evidence:

- breadth: `files_consulted` >= 25
- cross-repo: two or more distinct `repos`
- repeated reread: the same path counted >= 3 in `reread`

Invalid signals fail closed with `RETRIEVAL=BLOCKED`.

## Canonical exemplar hygiene

Agents learn from repository examples as well as prose. Do not let legacy/generated/deprecated code become an accidental design authority.

For complex repositories where imitation risk is material:
- point knowledge/index/navigation to representative canonical implementations when practical;
- mark generated, vendored, deprecated, migration-only, or intentionally legacy areas so they are not treated as preferred examples;
- when multiple patterns coexist, identify the current preferred contract rather than forcing the agent to infer it from frequency;
- remove or update stale exemplar references when the preferred architecture changes.

This is routing metadata, not permission to duplicate implementation documentation or load exemplar code into every task.

## Documentation triggers

Update durable documentation when a change alters:
- public API/CLI
- install/upgrade/uninstall behavior
- configuration or persisted data
- security model
- operational procedures
- supported platforms
- release/rollback procedure

Avoid duplicating the same normative rule in multiple canonical locations. Link instead.

## Incident and field knowledge

Meaningful production/manual findings should become one or more of:
- regression scenario
- test invariant
- runbook improvement
- RCA
- ADR
- product requirement/specification clarification

Knowledge that exists only in a chat is not durable engineering state.
