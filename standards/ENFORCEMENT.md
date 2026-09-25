# Engineering System Enforcement

The Engineering System is useful only if agents and CI actually consume it.

## Applicability

The global default applies to every software/product engineering project the owner works on with AI assistance, regardless of GitHub organization, repository owner, product name, or project location.

The canonical standard lives in `datarelay-labs/engineering-system`.

## Core vs adapters

The Engineering System core is agent-agnostic:
- `standards/*`
- `.engineering/*` contract
- deterministic GitHub gates
- evidence and release rules

Tool-specific behavior is implemented through adapters:
- ChatGPT Custom/Project Instruction
- Cursor User Rule
- repository `.cursor/rules/engineering-system.mdc`
- future AI-tool adapters

An adapter may translate the core rules into tool-specific instructions but must not weaken them.

## Context-loading enforcement

Always read:
- `AGENTS.md`
- `.engineering/project.yaml`

Read only when relevant:
- `.engineering/tests.yaml` for implementation/debugging/testing
- `.engineering/release.yaml` for release/version/artifact work
- the relevant Engineering System standard(s)
- relevant product specification/ADR/runbook material

Do not preload every standard, Wiki page, archive, or historical discussion.

## Mutating side effects and safe retry

A transient transport failure does not prove that an external mutation failed.

For agent/coordinator actions that may create, send, publish, deploy, modify, or delete external state, classify the action as one of:

- `READ_ONLY`
- `IDEMPOTENT`
- `IDEMPOTENCY_KEYED`
- `NON_IDEMPOTENT`
- `IRREVERSIBLE`

Retry policy:

- read-only/idempotent actions may use bounded transient retry when other safety rules allow;
- idempotency-keyed actions must reuse a stable operation key for the same intended mutation;
- after timeout, disconnect, or another ambiguous outcome on a mutating action, reconcile the authoritative external state before retrying;
- do not blindly repeat non-idempotent/irreversible actions such as sending a message, creating duplicate Issues/PRs/resources, publishing/releasing, deploying, rotating credentials, or destructive mutation;
- if actual outcome cannot be determined safely, yield/block for reconciliation rather than guessing.

This rule composes with approval, permission, and replay protections; it does not replace them.

Coordinator reconciliation for that classification is `python3 tools/coordinator.py plan --facts <facts.json>`. The planner reads structured facts and emits one bounded decision. Ambiguous mutation facts yield `RECONCILE_AMBIGUOUS` before retry. The planner itself does not spawn processes, mutate GitHub, merge, send messages, or stop sessions.

A bounded watch re-entry is `python3 tools/coordinator_watch.py evaluate --facts <facts.json> --watch-state <state.json>`. The evaluator calls the planner and emits one result. It does not spawn processes, call GitHub, merge, send Telegram, or start/stop Cursor. Ambiguous mutation facts yield `BLOCK_RECONCILIATION` and authorize no retry. A watch class cannot emit another class's authority. Caller subject overrides, observations older than the latest accepted observation, and an exhausted transient retry budget fail closed. Notification keys include the coordinator decision. Host scheduling and delivery stay outside this evaluator.

The run-once host is `python3 tools/coordinator_watch_host.py run-once --request <request.json>`. It locks one canonical watch identity, calls the evaluator, refetches authoritative facts with `collect_authoritative` through trusted `/usr/bin/gh`, and delivers at most one typed action after `send_effect` returns a durable receipt. The dispatch is reserved for that effect before send and consumed only after the receipt. Caller `PATH`, commands, URLs, fact files, packet machine-fact text, and non-canonical state paths fail closed. A held lock, stale revision, stale subject, stale branch, pinned worktree branch or HEAD mismatch, missing or different claim worktree, resource or WIP denial, ambiguous send, foreign dispatch reservation, or missing trusted dispatch yields zero actions and does not stop unrelated sessions.

Immediately before an Issue/PR write or publication effect, classify it with `python3 tools/worker_adapter.py evaluate --request-json <facts.json>`. Proceed only on `APPLIED`. `STALE_WORKER` authorizes no write. `RECONCILE_AMBIGUOUS` must be reconciled before another attempt. The adapter does not mint dispatch authority or stop unrelated sessions.

## Agent-facing tool contract

Tools intended for AI agents should be designed for reliable selection and bounded context use:

- give tools clear, non-overlapping purposes and stable names/namespaces;
- prefer typed/machine-readable inputs and stable machine-readable error reasons;
- default to bounded output with filters, range selection, pagination, or concise summaries;
- retain a deterministic path to retrieve fuller/raw evidence when needed;
- avoid exposing broad tool/MCP surfaces when the current task needs only a small subset;
- treat tool-selection success and task completion as quality signals, not API success alone.

Do not add another tool when an existing task-relevant tool already provides the same authority and oracle.

## Repository entrypoint

Every adopted repository must contain:
- `AGENTS.md`
- `.engineering/project.yaml`
- `.engineering/tests.yaml`
- `.engineering/release.yaml`
- `.cursor/rules/engineering-system.mdc`

Engineering System >=1.3.0 additionally requires:
- `.cursor/commands/resume.md`
- `.github/ISSUE_TEMPLATE/ai-work-packet.md`

Engineering System >=1.4.0 managed adoption additionally requires:
- immutable `engineering_system.baseline`
- `.github/workflows/engineering-system.yml` pinned to that baseline

Engineering System >=1.5.0 managed adoption additionally records:
- explicit operations posture (production or non-production)
- incident/runbook requirements for production-oriented projects
- concrete native CI workflow ownership when `ci_mode=native`
- merge-gate enforcement state as `verified`, `advisory`, or `unknown`
- design/incident routing in the repository AGENTS entrypoint

Engineering System >=1.6.0 managed adoption additionally:
- wires `.github/workflows/enforcement-check.yml` through the pinned baseline
- compares declared merge-gate state with observable live GitHub rulesets on pull requests
- treats a declared `verified` state that cannot be verified as a failure
- treats observable `verified` vs `advisory` drift as a configuration failure
- permits `unknown` when GitHub enforcement visibility genuinely is unavailable

Use `standards/ADOPTION.md` and `tools/adopt.py` for managed adoption. Existing repository rules must be classified before destructive cleanup.

## Deterministic CI compliance

Adopted repositories should validate required Engineering System files and use fast affected PR checks.

Shared workflows must not force duplicate project-native qualification. Expensive full-suite/lifecycle/platform/performance/operational gates belong at the appropriate release boundary.

## Fail-closed behavior

Missing or contradictory mandatory engineering context is a configuration defect. Do not silently continue as if repository-specific compliance had been established.

## Enforcement chain

```text
global AI adapter
 -> repository AGENTS / tool adapter
 -> minimal relevant .engineering metadata
 -> pinned Engineering System
 -> deterministic affected checks
 -> release preflight
 -> exact-candidate qualification
```


## Live GitHub enforcement reconciliation

Workflow existence is not evidence that merge blocking is active.

For 1.6+ adopted repositories:

```text
project.yaml merge_gate_status
        +
live default-branch GitHub rulesets
        ↓
reconciliation
```

`verified` means an active default-branch rule requires both pull requests and one or more status checks. `advisory` means the repository automation may run but those merge gates are not both enforced. `unknown` is reserved for cases where live enforcement cannot be observed safely.

The reconciliation workflow is read-only. The Engineering System does not silently create or relax GitHub administrative rulesets.
