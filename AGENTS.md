# Engineering System Repository Rules

This repository defines the canonical Solo AI Engineering System:
https://github.com/datarelay-labs/engineering-system

## Context budget

Always read:
1. `AGENTS.md`
2. `.engineering/project.yaml`

Read only when the task requires it:
- `.engineering/tests.yaml` for implementation/debugging/testing
- `.engineering/release.yaml` for release/version/artifact work
- one relevant Engineering System standard plus only task-relevant product/spec/ADR/runbook material
- `.engineering/knowledge.yaml` when present, for domain routing. Freshness is `python3 tools/knowledge-contract.py check`. Retrieval stays local unless `python3 tools/knowledge-contract.py route` reports `RETRIEVAL=ESCALATE`.
- `.engineering/runtime.yaml` when validating a running worktree. Resolve health, smoke, E2E, and additive capabilities with `python3 tools/runtime-contract.py check`.

Use the minimum sufficient context and reasoning. Expand only when a concrete blocker, failed check, or unresolved design question requires it. Do not preload all standards, Wiki pages, archives, historical discussions, or old agent transcripts.

## Execution rules

- Classify the change and affected domains/contracts/security/operations.
- Apply `standards/DESIGN.md` for material design-bearing changes.
- Apply `standards/OPERATIONS.md` for production-impacting failures; preserve evidence before mutation.
- Inspect only the affected implementation/tests before editing and make the smallest correct change.
- On an existing branch or PR, start with `git diff --name-only`/`git diff --stat` against the base and inspect changed files first; expand to call-sites/dependencies only when evidence requires it.
- Treat `.engineering/tests.yaml` as an ordered-cost manifest, not a list to execute from the first entry: prefer `agent_default: true` and the lowest explicit `cost`; when metadata is absent, treat static/unit as cheap, component/feature as medium, and integration/lifecycle/performance/e2e as expensive. Do not auto-run expensive/full checks for metadata-only changes.
- For verbose commands, write full output to a log file and return only exit status plus focused `grep`/`tail` evidence; read more only on failure or ambiguity.
- Run the cheapest affected deterministic validation first; do not duplicate equivalent native/shared gates.
- Stop expensive downstream qualification after a blocking deterministic failure.
- Add durable regression coverage for bug fixes when practical.
- Never weaken validation or report unexecuted, blocked, historical, or different-HEAD evidence as PASS.
- Before merge or terminal completion, inspect actionable review feedback and fix/revalidate or evidence-disposition every actionable finding.
- Do not spend coding-agent model time polling CI, review, or another machine-observable external wait. Persist concise waiting state and yield to coordinator/automation for re-entry.
- Keep schemas/templates/workflows backward-aware.

For repository adoption or managed upgrades, follow `standards/ADOPTION.md`; preserve project-specific/stricter rules and fail closed on ambiguous destructive changes.

Adopted projects pin `engineering_system.version` and an immutable `engineering_system.baseline` SHA in `.engineering/project.yaml`. This canonical repository currently ships Engineering System 1.6.5; do not treat an older same-major pin as current without matching the immutable baseline.

If mandatory context is missing or contradictory, report the configuration defect instead of guessing.

Cursor receives the always-applied `.cursor/rules/engineering-system.mdc` adapter; that adapter must stay intentionally small and defer detail to this file and task-relevant standards.
