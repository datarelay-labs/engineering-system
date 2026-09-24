# Repository Engineering Rules

This repository follows the canonical Engineering System:
https://github.com/datarelay-labs/engineering-system

## Context budget

Always read:
1. `AGENTS.md`
2. `.engineering/project.yaml`

Read only when relevant:
- `.engineering/tests.yaml` for implementation/debugging/testing
- `.engineering/release.yaml` for release/version/artifact work
- one task-relevant Engineering System standard plus only the product/spec/ADR/runbook material required by the change
- `.engineering/knowledge.yaml` when present, for domain routing. Freshness is `python3 tools/knowledge-contract.py check`. Retrieval stays local unless `python3 tools/knowledge-contract.py route` reports `RETRIEVAL=ESCALATE`.
- `.engineering/runtime.yaml` when validating a running worktree. Resolve health, smoke, E2E, and additive capabilities with `python3 tools/runtime-contract.py check`.
- `.engineering/skills.yaml` when present, for progressive-disclosure skills/hooks and permission profiles. Validate with `python3 tools/skills-contract.py check`. Authorization is verification-only via `python3 tools/skills-contract.py authorize` against host-administered adapter provenance and fails closed as `BOUNDARY_UNAVAILABLE` when that provenance is unavailable; see `standards/SKILLS.md`. No production HMAC/`bind` mint.

Use the minimum sufficient context and reasoning. Expand only for a concrete blocker, failed check, or unresolved design question. Do not preload all standards, Wiki pages, archives, historical discussions, or old agent transcripts.

When resuming a workstream, resolve this repository first, load only its single matching active AI Work Packet, verify actual branch/HEAD/state, and continue from the coherent Next Action.

## Execution rules

- Classify the change and affected domains/contracts/security/operations.
- Before handing implementation to Cursor/coding agent, apply the canonical Work Packet sizing contract: do not map one GitHub Issue to one Cursor job by default; `BATCH` adjacent micro-issues/findings, `KEEP` one medium-sized coherent outcome, and `SPLIT` unrelated or context-exhausting scope. Preserve enough session context for implementation plus deterministic validation. Record the decision with `python3 tools/work_admission.py size` when the coordinator needs machine-readable evidence.
- Default remains sequential. Before starting a second worker, run `python3 tools/work_admission.py admit` against claim/worktree/resource facts and obey DENY. Never stop or mutate unrelated sessions to create capacity.
- For `CHANGE_RISK=HIGH|CRITICAL` terminal PASS, require `python3 tools/independent_verifier.py verify` with verifier identity/context distinct from the implementer and exact subject-HEAD evidence. Do not treat implementer self-report as the completion oracle.
- Treat the Work Packet `Next Action` as the next bounded outcome/execution bundle with a completion oracle, not a micro-step. Keep tangential discoveries in linked follow-up Issues unless the coordinator explicitly re-sizes the active packet; repeated materially identical failures require a strategy change rather than blind retry.
- Apply the canonical sufficiency gate: once the completion contract passes and no blocking finding remains, stop that workstream and return to roadmap priority. Do not continue open-ended hardening/auditing for non-blocking improvements; record them as follow-up work.
- Use Work Packet priority only for scheduling and `CHANGE_RISK` only for verification depth. Before finalizing mutable or external actions, reject stale workers by re-checking the current Work Packet intent revision and mutable evidence; reconcile ambiguous mutating outcomes before retrying.
- For external agent tools/MCP/plugins, follow the canonical security provenance/authority and agent-tool ergonomics rules; expose only the minimum task-relevant toolset.
- Apply `standards/DESIGN.md` for material design-bearing changes.
- Apply `standards/OPERATIONS.md` for production-impacting failures and preserve evidence before mutation. Check an Incident Packet with `python3 tools/incident-evidence.py check-packet` and capture bounded read-only core evidence with `python3 tools/incident-evidence.py capture` from the canonical Engineering System checkout before mutation. Incident Packet state never grants execution authority; `SAFETY_FREEZE=ON` only narrows it.
- Inspect affected implementation/tests and make the smallest correct change.
- On an existing branch or PR, start with `git diff --name-only`/`git diff --stat` against the base and inspect changed files first; expand to call-sites/dependencies only when evidence requires it.
- Treat `.engineering/tests.yaml` as an ordered-cost manifest, not a list to execute from the first entry: prefer `agent_default: true` and the lowest explicit `cost`; when metadata is absent, treat static/unit as cheap, component/feature as medium, and integration/lifecycle/performance/e2e as expensive. Do not auto-run expensive/full checks for metadata-only changes.
- For verbose commands, write full output to a log file and return only exit status plus focused `grep`/`tail` evidence; read more only on failure or ambiguity.
- Run the cheapest affected deterministic validation first.
- Do not duplicate equivalent native/shared CI or run expensive downstream qualification after a blocking deterministic failure.
- Add durable regression coverage for bugs when practical.
- Never weaken validation or claim PASS from unexecuted, blocked, historical, or different-HEAD evidence.
- Before merge or terminal completion, resolve every actionable review finding with revalidation or an evidence-backed disposition.
- Do not keep a coding-agent session alive polling CI/review/external waits; persist concise state and yield to coordinator/automation.
- If mandatory engineering context is missing or contradictory, fail closed instead of guessing.

For adoption or managed upgrades, follow `standards/ADOPTION.md`, preserve project-specific/stricter rules, and qualify the result deterministically.

Adopted projects pin `engineering_system.version` and an immutable `engineering_system.baseline` SHA in `.engineering/project.yaml`. Managed upgrades must keep that version/baseline identity aligned with the canonical Engineering System release (currently 1.6.5) rather than assuming same-major pins are current.

Tool-specific adapters may change syntax but must not weaken these rules.
