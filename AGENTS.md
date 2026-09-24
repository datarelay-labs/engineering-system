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
- `.engineering/skills.yaml` when present, for progressive-disclosure skills/hooks and permission profiles. Validate with `python3 tools/skills-contract.py check`. Authorization is verification-only via `python3 tools/skills-contract.py authorize` against host-administered adapter provenance and fails closed as `BOUNDARY_UNAVAILABLE` when that provenance is unavailable; see `standards/SKILLS.md`. No production HMAC/`bind` mint.

Use the minimum sufficient context and reasoning. Expand only when a concrete blocker, failed check, or unresolved design question requires it. Do not preload all standards, Wiki pages, archives, historical discussions, or old agent transcripts.

## Execution rules

- Classify the change and affected domains/contracts/security/operations.
- Before handing implementation to Cursor/coding agent, apply the Work Packet sizing contract in `standards/SESSION_CONTINUITY.md`: do not map one GitHub Issue to one Cursor job by default; `BATCH` adjacent micro-issues/findings, `KEEP` one medium-sized coherent outcome, and `SPLIT` unrelated or context-exhausting scope. Preserve enough session context for implementation plus deterministic validation. Record the decision with `python3 tools/work_admission.py size` when the coordinator needs machine-readable evidence.
- Default remains sequential. Before starting a second worker, run `python3 tools/work_admission.py admit` against claim/worktree/resource facts and obey DENY. Never stop or mutate unrelated sessions to create capacity.
- For `CHANGE_RISK=HIGH|CRITICAL` terminal PASS, require `python3 tools/independent_verifier.py verify` with verifier identity/context distinct from the implementer and exact subject-HEAD evidence. Do not treat implementer self-report as the completion oracle.
- Reconcile one Work Packet's next action with `python3 tools/coordinator.py plan --facts <facts.json>`. The planner is pure: one bounded decision, no worker launch, GitHub mutation, merge, notification send, or session stop.
- Before an external Issue/PR write, run `python3 tools/worker_adapter.py evaluate --request-json <facts.json>` against a fresh authoritative read. Proceed only on `APPLIED`. A revision or head mismatch is `STALE_WORKER` and must not write.
- Treat the Work Packet `Next Action` as the next bounded outcome/execution bundle with a completion oracle, not a micro-step. Keep tangential discoveries in linked follow-up Issues unless the coordinator explicitly re-sizes the active packet; repeated materially identical failures require a strategy change rather than blind retry.
- Apply the sufficiency gate in `standards/SESSION_CONTINUITY.md`: once the completion contract passes and no blocking finding remains, stop that workstream and return to roadmap priority. Do not continue open-ended hardening/auditing for non-blocking improvements; record them as follow-up work.
- Use Work Packet priority only for scheduling and `CHANGE_RISK` only for verification depth. Before finalizing mutable or external actions, reject stale workers by re-checking the current Work Packet intent revision and mutable evidence; reconcile ambiguous mutating outcomes before retrying.
- For external agent tools/MCP/plugins, apply `standards/SECURITY.md` provenance/authority rules and `standards/ENFORCEMENT.md` agent-tool ergonomics; expose only the minimum task-relevant toolset.
- Apply `standards/DESIGN.md` for material design-bearing changes.
- Apply `standards/OPERATIONS.md` for production-impacting failures; preserve evidence before mutation. Check an Incident Packet with `python3 tools/incident-evidence.py check-packet` and capture bounded read-only core evidence with `python3 tools/incident-evidence.py capture` before mutation. Incident Packet state never grants execution authority; `SAFETY_FREEZE=ON` only narrows it. Collect one authorized runtime evidence command with `python3 tools/runtime_evidence.py collect`.
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
