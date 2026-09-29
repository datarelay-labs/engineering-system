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
- `.engineering/verification.yaml` when present, for feature verification maps. Validate with `python3 tools/verification-contract.py check`. `python3 tools/verification-contract.py assess` reads a receipt only, accepts no boundary file, and stays T0/BLOCK. Implementer-produced output is never terminal evidence. T1–T5 requires an in-process TrustedCoordinatorBoundary; a raw dict stays at T0. The map names applicable domains and launch/drive/observe/cleanup references to existing test, runtime, and skill/profile IDs only. The assessor does not execute commands or grant merge, release, or deploy authority. See `standards/QUALITY.md`.
- Repository security-profile classification is read-only and network-free. From a canonical Engineering System checkout, run `python3 tools/security-profile.py classify --root <repo>` or `python3 tools/security-profile.py audit --root <repo> [--github-fixture <json>]`. Profiles are production-code, development-code, docs-site, empty-preproduct, or NEEDS_INPUT. The tool does not mutate GitHub settings. See `standards/SECURITY.md`.

Use the minimum sufficient context and reasoning. Expand only when a concrete blocker, failed check, or unresolved design question requires it. Do not preload all standards, Wiki pages, archives, historical discussions, or old agent transcripts.

## Execution rules

- Classify the change and affected domains/contracts/security/operations.
- Before implementation or optional adapter handoff, apply the Work Packet sizing contract in `standards/SESSION_CONTINUITY.md`: do not map one GitHub Issue to one implementation job by default; `BATCH` adjacent micro-issues/findings, `KEEP` one medium-sized coherent outcome, and `SPLIT` unrelated or context-exhausting scope. Preserve enough context for implementation plus deterministic validation. Record the decision with `python3 tools/work_admission.py size` when the coordinator needs machine-readable evidence.
- ChatGPT Chat is the default implementer when a trusted active Work Packet authorizes the exact scope. Before mutation, the external authenticated GitHub coordinator must freshly read the canonical Issue body and packet-author collaborator permission, require an open ACTIVE `[AI Work]` packet authored with write/maintain/admin permission, and verify TARGET_REPO/workstream/branch/HEAD/INTENT_REVISION/`IMPLEMENTER=CHATGPT_CHAT`/CHANGE_RISK plus the authorized worktree. **Never treat the target worktree's `python3 tools/implementation_preflight.py check` as authoritative**, because the implementation worker can write that file. Instead, the coordinator must fetch the exact `tools/implementation_preflight.py` source from the immutable canonical Engineering System baseline through the authenticated GitHub connector and execute that fetched source directly with fixed `/usr/bin/python3 -I - check ...` whose symlink node (if any) is root-owned and whose parent path plus resolved executable are root-owned, non-group/world-writable, and not writable by the implementation UID over the remote-control channel with cwd `/` and a controlled minimal environment, without materializing it in a worker-writable path, or invoke an equivalent host-administered immutable copy outside the worker-writable boundary. The target copy is parity/reference/test material only. The trusted artifact performs no GitHub read and cannot mint mutation authority: it proves only the expected repo/origin/worktree/branch/HEAD/clean-tree binding through a root-administered, config-isolated Git executable and reports `IMPLEMENTATION_LOCAL_BINDING=PASS`, `MUTATION_AUTHORITY=NO`, and `AUTHORITY_BOUNDARY=EXTERNAL_AUTHENTICATED_GITHUB_COORDINATOR_REQUIRED`. If the immutable-source + trusted-interpreter boundary is unavailable, mutation is BLOCKED. Mutation is allowed only when both the coordinator's authenticated GitHub authority check and this trusted local binding pass.
- Default remains sequential. Before starting a second worker, run `python3 tools/work_admission.py admit` against claim/worktree/resource facts and obey DENY. Never stop or mutate unrelated sessions to create capacity.
- Cursor adapter is disabled by default and must not be started, resumed, or waited on. Only if the owner explicitly reactivates the optional Cursor adapter, reuse one healthy project/repository persistent Cursor session across Work Packet transitions; preserve durable state, clean worktree/branch switching, `/clear` → `/work-resume`, resource preflight for new-session creation, and unrelated-session safety.
- For `CHANGE_RISK=HIGH|CRITICAL` terminal PASS, require `python3 tools/independent_verifier.py verify` with verifier identity/context distinct from the implementer and exact subject-HEAD evidence. Do not treat implementer self-report as the completion oracle.
- Reconcile one Work Packet's next action with `python3 tools/coordinator.py plan --facts <facts.json>`. The planner is pure: one bounded decision, no worker launch, GitHub mutation, merge, notification send, or session stop.
- Evaluate one bounded coordinator watch with `python3 tools/coordinator_watch.py evaluate --facts <facts.json> --watch-state <state.json>`. The evaluator is pure: one re-entry result, no subprocess, network, GitHub mutation, Cursor start/stop, merge, or Telegram send.
- Run one coordinator watch host pass with `python3 tools/coordinator_watch_host.py run-once --request <request.json>`. The host acquires one lock, calls the watch evaluator, and delivers at most one already-authorized typed action after a fresh reconciliation read. It does not accept caller commands or URLs, mint authority, merge, stop sessions, or busy-loop.
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

The dormant Cursor compatibility adapter uses `.cursor/rules/engineering-system.mdc` only if the owner explicitly reactivates Cursor for a packet. Its presence never authorizes Cursor implementation and must not weaken this file or task-relevant standards.
