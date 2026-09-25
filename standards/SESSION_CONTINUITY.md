# AI Session Continuity & Work Packet Standard

## Purpose

Long-running AI-assisted engineering must not depend on replaying or copying old ChatGPT/Cursor conversations.

Conversation history is temporary working context. Durable engineering state belongs in Git, GitHub, tests, specifications, and a small repository-scoped working-state record.

The default working-state record is an **AI Work Packet** stored as a GitHub Issue in the same repository as the work it describes.

## Why repository-scoped

Do not use one global cross-project handoff document.

The repository is the namespace boundary:

```text
project repository
  -> canonical code/spec/tests
  -> repository-scoped AI Work Packet Issues
```

This prevents an agent working on one project from accidentally loading another project's state.

Do not create a separate workspace repository merely to hold handoffs unless a future requirement cannot be satisfied by repository-scoped Issues.

## Why Issue state, not a committed HANDOFF file

Release-candidate repositories may require exact-HEAD qualification.

Updating a committed handoff file changes the product Git SHA and can invalidate exact-HEAD evidence.

Updating a GitHub Issue does not change the repository source HEAD.

Therefore live session state must not be committed to the qualified product branch solely for AI continuity.

## Unit of continuity

Use one Work Packet per active **workstream**, not one packet per repository.

Examples:

```text
[AI Work] v2.4.0 Release Closure
[AI Work] Website Refresh
[AI Work] MCP Design
```

Multiple workstreams may coexist safely when packet selection is deterministic.


## Work Packet sizing before implementation handoff

A GitHub Issue is coordination state, not automatically the unit of Cursor execution. Do **not** hand Cursor one tiny Issue/finding at a time, and do not combine unrelated outcomes into one oversized session.

Before every implementation handoff, the coordinator must classify the next executable scope as:

- `BATCH` — too small by itself. Combine adjacent small Issues/findings when they share the same repository area, implementation context, owner intent, and validation oracle. Multiple GitHub Issues may be referenced by one medium-sized Work Packet/Next Action.
- `KEEP` — right-sized. Prefer one primary independently verifiable outcome with a few tightly coupled subgoals that one persistent coding-agent session can implement, test, and hand back while retaining enough context for deterministic validation.
- `SPLIT` — too large. Split when the scope contains multiple independently releasable outcomes, unrelated domains/owners, materially different approval or validation gates, unclear rollback boundaries, or is likely to exhaust the session context before implementation **and** validation finish.

Default handoff behavior:

1. Do not use “one GitHub Issue = one Cursor job” as a rule.
2. Batch micro-fixes and closely related findings into a coherent medium-sized packet instead of creating repeated short Cursor cycles and notifications.
3. Keep one primary outcome; a small number of tightly coupled subgoals is preferred over either a single trivial edit or a broad multi-domain program.
4. File count and LOC are advisory only. Structural coupling, independent verification, approval boundaries, rollback boundaries, and context budget determine size.
5. Reserve context for implementation **and** testing/review. If implementation alone is expected to consume the reliable session context, split before handoff.
6. If scope materially expands during execution, the coding agent must stop absorbing unrelated work, update the Work Packet, and yield for coordinator re-sizing.
7. Record the sizing decision in the packet’s current handoff state, for example:

```text
WORK_PACKET_SIZING=KEEP
INCLUDED_ISSUES=#101,#102,#105
SIZING_REASON=Same subsystem and validation oracle; one independently verifiable outcome.
```

The goal is a **medium-sized, coherent, independently verifiable handoff**: large enough to avoid handoff/review overhead, small enough to complete implementation plus deterministic validation in one bounded session.

### Initial sizing calibration

Use these as **soft operating defaults**, not hard limits. Structural coupling and a clear completion oracle override raw counts.

- Preferred center: roughly **one hour of competent human engineering work** for the primary outcome.
- Initial `KEEP` band: roughly **30–120 minutes human-equivalent effort**, one logical outcome, and one coherent validation plan.
- A change of **a few hundred hand-written lines** is normally still in the target zone when it stays within one outcome. Generated files, lockfiles, snapshots, and mechanical propagation do not determine size by themselves.
- `BATCH` when a task is materially below that band and adjacent findings share the same subsystem, implementation context, and validation oracle. GitHub Issue count is not a sizing metric; one handoff may reference several small Issues.
- `SPLIT` when the work is likely to exceed roughly two hours of human-equivalent effort, contains multiple independent outcomes/rollback boundaries, or would consume the useful agent context before targeted validation and review can finish.
- During execution, if context usage is already high and a distinct implementation subgoal remains, persist the verified state and continue that subgoal in a fresh bounded session rather than forcing the original session through compaction/noise. Finishing the current tightly coupled validation step is preferred over splitting in the middle of an atomic check.

These values are calibration defaults, not universal constants. P0/P1 efficiency telemetry should measure rework, validation failures, context pressure, and handoff overhead; revise the band from observed outcomes rather than increasing task size merely because a model can technically run longer.

## Required identity fields

Every packet body begins with:

```text
PACKET_VERSION=2
TARGET_REPO=owner/repository
WORKSTREAM=<stable-slug>
STATUS=ACTIVE|PAUSED|BLOCKED|COMPLETE
BRANCH=<branch-name|N/A>
TASK_KIND=DESIGN|DEVELOPMENT|TEST|REVIEW|RELEASE|OPERATIONS|ADOPTION|DOCUMENTATION|CLEANUP|MIXED
OWNER_INTENT=<one concise line describing the owner's current explicit request>
LAST_VERIFIED_HEAD=<40-char-sha|UNKNOWN>
```

`LAST_VERIFIED_HEAD` is evidence of the last observed state, not authority. The current repository state must be re-verified on resume.

`TASK_KIND` and `OWNER_INTENT` describe the current bounded handoff, not the lifetime purpose of the workstream. Refresh them whenever the owner's explicit request changes materially.


### Optional coordinator fields

New or actively coordinated packets should record these compact fields when the coordinator/orchestrator uses them:

```text
PRIORITY=NORMAL
INTENT_REVISION=1
CHANGE_RISK=MEDIUM
```

- `PRIORITY=URGENT|HIGH|NORMAL|LOW` controls scheduling order only. It is not a security/risk rating.
- `INTENT_REVISION` is a monotonically increasing integer for material handoff changes.
- `CHANGE_RISK=LOW|MEDIUM|HIGH|CRITICAL` controls verification/approval depth, not scheduling priority.

Legacy packets without these fields remain valid. A coordinator may conservatively treat missing `PRIORITY` as `NORMAL`, missing `CHANGE_RISK` as `MEDIUM`, and establish `INTENT_REVISION=1` at the next material handoff update.

## Required sections

Keep the packet short and current:

```text
Goal
Current State
Next Action
Constraints
Canonical References
Latest Evidence
Blockers
```

### Goal

One concise statement of the workstream outcome.

### Current State

Only facts needed to resume now.

### Next Action

State the next **bounded outcome / execution bundle**, not a single shell command, one tiny Issue, or one implementation micro-step. It must be large enough to avoid repeated handoff overhead and small enough to finish implementation plus deterministic validation under the Work Packet sizing contract.

A coding-agent session, PR, or process restart is an execution detail. The durable unit is the intended outcome plus its completion oracle. Do not rewrite the packet after every trivial edit merely because one agent turn or subprocess ended.

### Completion Contract

For non-trivial work, record the smallest observable completion contract that lets an independent verifier decide whether the outcome is done:

- expected observable behavior or state;
- deterministic test/oracle or explicit manual evidence when deterministic proof is impossible;
- prohibited regressions or invariants that must remain true;
- exact-head or runtime evidence required before terminal PASS.

The implementing agent's self-report is never the completion oracle by itself.

### Follow-up Discoveries

Keep tangential discoveries out of the active scope unless the coordinator explicitly re-sizes the packet. Record meaningful out-of-scope bugs, refactors, security findings, or opportunities as linked follow-up Issues/Work Packets with enough evidence to reproduce or triage them. Do not silently absorb them into the current implementation merely because they were discovered nearby.

### Constraints

Current scope/safety restrictions that materially affect execution.

### Canonical References

Paths or links to authoritative repository artifacts. Reference them; do not copy their contents into the packet.

### Latest Evidence

Compact executed evidence such as exact HEAD, targeted test result, CI run/PR link, or explicit NOT_RUN/BLOCKED state.

### Blockers

Only current blockers.

## Portfolio priority and safe preemption

Sizing answers “how much work belongs in one handoff”; priority answers “which eligible outcome runs next.” Keep them separate.

Coordinator scheduling rules:

1. Respect explicit owner priority first.
2. Run only dependency-eligible work whose required environment/authority is available.
3. Within the same priority, prefer the oldest eligible packet unless a repository-specific policy says otherwise.
4. Risk/severity may increase verification depth but must not silently become scheduling priority.
5. When the owner raises another packet's priority or an incident/release blocker becomes explicitly prioritized, lower-priority work may be preempted at the next safe checkpoint.
6. Preemption must preserve recoverable state: finish/abort the current atomic operation safely, persist current evidence, mark/yield the packet, and release worker claims when safe. Do not terminate in the middle of an irreversible/external mutation merely to switch tasks.
7. Preempted work remains durable and resumable; it does not become COMPLETE or discarded.

A non-empty follow-up backlog does not entitle the current theme to retain priority after sufficiency is reached.

## Intent revision and stale-worker rejection

A coding-agent worker must not finalize an obsolete handoff after the owner/coordinator materially changes direction.

- Increment `INTENT_REVISION` whenever `Goal`, `OWNER_INTENT`, `TASK_KIND`, `Next Action`, material constraints, completion contract, or approval boundary changes.
- Evidence refreshes, comments, or wording changes that do not alter executable intent need not increment it.
- A worker records the revision it started from.
- Before commit/push, merge request creation/update, external write, production action, release/deploy action, or terminal completion, re-read the authoritative Work Packet and compare the revision.
- Revision mismatch means `STALE_WORKER`: do not finalize the old intent. Persist useful recoverable state and yield to the coordinator.
- Steering messages or conversation context are advisory; the repository-scoped Work Packet revision is the durable authority.

For legacy packets without a revision, establish one before the next material implementation handoff rather than inventing freshness from chat history.

## Owner-intent synchronization

Before handing work to an implementation agent, the coordinating agent must synchronize the packet with the owner's latest explicit request:

1. Set `TASK_KIND` to the current execution phase.
2. Set `OWNER_INTENT` to one concise statement of what the owner is asking for now.
3. Ensure `Next Action` directly advances both the workstream `Goal` and `OWNER_INTENT`.
4. Do not silently substitute an older release, cleanup, or validation step merely because it was previously pending.
5. If the new request is still the same workstream, update the existing packet. If it is a genuinely independent workstream, create a separate packet.
6. If `Goal`, `OWNER_INTENT`, `TASK_KIND`, and `Next Action` materially conflict, do not execute the packet. Report `WORK_PACKET_SCOPE_MISMATCH` and obtain or record the minimum correction needed.

`STATUS` is deliberately small and fixed. Do not invent transient values such as `CURSOR_READY`, `WAITING`, or `DONE`.

- `ACTIVE` — work is runnable or waiting on a machine-observable condition that can be resumed automatically.
- `PAUSED` — the owner intentionally paused the workstream.
- `BLOCKED` — progress requires a human/external action or a required execution environment is unavailable.
- `COMPLETE` — terminal; no executable `Next Action` remains.

"Ready for Cursor" is represented by `STATUS=ACTIVE` plus a valid `Next Action`, not by a new status value.

Packet version 1 is legacy-compatible. Agents may resume a valid v1 packet, but should migrate it to v2 fields on the next meaningful packet update rather than blocking solely because `TASK_KIND` or `OWNER_INTENT` is absent.

## What must not be copied into a Work Packet

Do not paste:

- old conversations
- previous handoff prompts
- previous Cursor prompts
- Product Master/specification contents
- Engineering System contents
- raw multi-megabyte logs
- complete historical phase reports
- secrets, credentials, private keys, tokens
- user-specific absolute local paths unless strictly necessary and non-sensitive

Link to canonical files, commits, PRs, CI runs, Issues, or retained evidence instead.

## Current-state overwrite rule

A Work Packet is a current-state record, not an append-only diary.

After meaningful progress:

- refresh `TASK_KIND` and `OWNER_INTENT` when the owner's request changes
- replace `Current State`
- replace `Next Action`
- replace `Latest Evidence`
- replace `Blockers`
- update `LAST_VERIFIED_HEAD`

Do not keep accumulating old phase text in the Issue body.

History already exists in Git commits, PRs, CI, Issue edits/comments, and closed Issues.

## Deterministic packet resolution

When resuming work:

1. Resolve the target repository first from the current Git remote or explicit user/project context.
2. Once resolved, do not search unrelated repositories.
3. Resolve the current branch when a local repository is available.
4. Read only open Issues whose title begins with `[AI Work]`. An `ai-work` label may be used as an optional search accelerator, but must not be required for correctness.
5. Require exact `TARGET_REPO` match.
6. Prefer an exact `BRANCH` match when branch context exists.
7. Require exactly one matching `STATUS=ACTIVE` packet. Reject non-canonical status values rather than treating them as aliases.
8. For packet v2, require `TASK_KIND` and `OWNER_INTENT`, and verify that `Next Action` directly advances the packet `Goal` and current owner intent. If they materially disagree, stop with `WORK_PACKET_SCOPE_MISMATCH`; do not repair the mismatch by searching unrelated chats, Athena, or other repositories.
9. For legacy packet v1, use `Goal` + `Next Action` conservatively and migrate the packet to v2 on the next meaningful update.
10. Zero matches: report no active packet; do not reconstruct state from guesses.
11. Multiple matches: fail closed and ask which workstream to use.
12. Verify actual repository branch, HEAD, dirty state, PR/CI state, and relevant canonical files before acting.

Never treat a stale packet HEAD as current truth.

## Resume context budget

After resolving Git identity and before ordinary work:

1. Check whether repository `AGENTS.md` and `.engineering/project.yaml` exist.
2. If they exist, read them first.
3. If the repository shows Engineering System adoption markers (for example `.engineering/`, `.cursor/rules/engineering-system.mdc`, managed `engineering-system.yml`, or session-continuity adapters) but mandatory `AGENTS.md` or `.engineering/project.yaml` is missing or unreadable, record `ENGINEERING_SYSTEM_ADOPTION=INCOMPLETE` and fail closed unless the selected packet is an explicit adoption-repair flow (`TASK_KIND=ADOPTION`).
4. If they are absent because the repository has not yet adopted the Engineering System or adoption is intentionally pending in a separate workstream/PR, record `ENGINEERING_SYSTEM_ADOPTION=ABSENT_OR_PENDING` and continue under the canonical Engineering System default. Do not create, merge, or modify adoption files unless the current Work Packet explicitly authorizes that work.
5. Read test/release metadata only when relevant and only if present for the adopted project state.
6. Read only canonical references required by `Next Action`.
7. Use minimum sufficient reasoning/context; do not request maximum reasoning by default.
8. Do not preload all references named in the packet.
9. Do not keep a coding-agent session alive polling CI, review, deployment, or another machine-observable external condition. Record a concise `WAITING_FOR_<CONDITION>` state and yield to coordinator/automation; the next resume re-checks the condition.
10. For an existing branch/PR, orient from the base diff first (`git diff --name-only`/`git diff --stat`) before broad repository search.
11. Bound tool output: retain verbose logs outside model context and surface exit status plus focused grep/tail evidence; expand only on failure or ambiguity.
12. Prefer a fresh coding-agent session for each new bounded `Next Action` only after `tools/cursor-resource-preflight.py` returns exit 0 (`PASS` or `WARN`). On `BLOCK`, do not create a new persistent session and do not stop, kill, or mutate existing sessions. Resource safety takes precedence over a fresh session. Reuse an already-running matching target session when that reuse is safe and semantically correct. Persistent sessions are for an in-flight action/process, not long-term memory.

Never-adopted repositories may continue under the canonical default. Incomplete adopted repositories must not silently continue ordinary work without mandatory project context.

The Work Packet replaces a large handoff; it must not become another large handoff.

## Sufficiency gate and anti-rabbit-hole rule

A Work Packet is not permission to keep improving the same area indefinitely. The coordinator must stop the current workstream when its declared completion contract is satisfied and no known blocking finding remains.

For implementation, hardening, audit, refactor, cleanup, and optimization work:

1. Define the **finish line before deep execution**: required behavior, verification surface, prohibited regressions, and the risk/finding classes that are blocking for this packet.
2. After each implementation/audit cycle, classify new findings:
   - **BLOCKING** — violates the completion contract, proves an exploitable/security-boundary failure, data-loss/corruption risk, public-contract regression, mandatory CI/release gate failure, or another explicitly declared packet invariant. Keep it in the active packet.
   - **FOLLOW_UP** — defense-in-depth, hypothetical hardening without a demonstrated path, cleanup, consistency improvement, optional optimization, or independently releasable work. Record it durably and do not keep the current packet open for it.
3. Once required evidence passes and no BLOCKING finding remains, mark the packet complete. Do not continue speculative auditing merely because additional improvements are imaginable.
4. A new audit round after sufficiency requires a new explicit trigger: a regression/failing oracle, incident evidence, a newly demonstrated exploit/path, a release requirement, or an explicit owner request.
5. Completion of one workstream returns control to roadmap/portfolio priority. Do not immediately reopen the same theme merely because its follow-up backlog is non-empty.

### Default depth budget

Use a soft default for ordinary bounded work:

```text
implementation -> independent audit -> corrective pass when needed -> verification -> stop
```

One implementation pass, one independent audit, and at most one ordinary corrective re-audit is the normal depth target. This is a **portfolio/attention budget**, not a safety waiver.

- If the corrective re-audit finds no BLOCKING defect, stop and route remaining findings to follow-up work.
- If a BLOCKING defect remains, continue only far enough to close that known blocker and verify its regression; do not restart an open-ended search for unrelated weaknesses in the same packet.
- Critical security, data-integrity, incident, or release-blocking evidence may exceed the soft depth budget, but the exception must name the concrete blocker. “More hardening may exist” is not sufficient.
- A packet that repeatedly discovers non-blocking improvements has reached diminishing returns for the current scope.

Recommended packet evidence:

```text
DEPTH_BUDGET=NORMAL
AUDIT_ROUNDS_USED=1
BLOCKING_FINDINGS=0
FOLLOW_UP_FINDINGS=2
STOP_DECISION=SUFFICIENCY_REACHED
```

The purpose is to optimize the whole engineering portfolio, not to maximize perfection in whichever subsystem was most recently inspected.

## Retry and stall circuit breaker

Repeated attempts are not progress by themselves.

Classify a failed attempt before retrying:

- **transient infrastructure** — transport/service/rate-limit/temporary runner failure; bounded retry with backoff is allowed;
- **environment/configuration** — missing dependency, wrong worktree, unavailable runtime, invalid credentials/permissions, or incompatible platform; fix the environment or yield with a concrete blocker;
- **semantic/deterministic** — the same code/test/design failure reproduces; do not repeatedly rerun or rephrase the same approach. Re-plan, reduce/reshape the outcome, switch to a fresh context when useful, or escalate the missing design decision;
- **ambiguous tool/model behavior** — preserve evidence and use a deterministic alternative or independent verifier rather than looping.

A task/session budget may stop work earlier. There is no universal retry count, but repeated materially identical semantic failure without new evidence must trigger a strategy change rather than another blind retry.

## Progress, stall, and worker lifecycle

A live process is not evidence of useful progress.

Meaningful progress is a machine-observable state/evidence delta such as:
- Git/worktree change that advances the declared outcome;
- new deterministic test/build/runtime evidence;
- a Work Packet milestone/state update;
- completion of a bounded tool/action;
- an explicit named external wait condition with durable re-entry state.

“Thinking”, process liveness, repeated identical logs, or repeated retries without new evidence are not progress.

A coordinator should apply a bounded stall budget appropriate to the task/environment. When no meaningful progress occurs within that budget:

1. reconcile Work Packet, Git/worktree, process, PR/CI/runtime, and dependency state;
2. distinguish a legitimate named external wait from a stalled worker;
3. restart/replace/yield the disposable worker when safe rather than leaving it indefinitely `running`;
4. preserve evidence and do not kill unrelated workers.

Worker ownership also has a lifecycle:

```text
CLAIMED -> ACTIVE -> WAIT/YIELD or RETRY -> COMPLETE/ABANDON/SUPERSEDED -> RELEASED
```

- Claims/locks are scoped to repository + workstream + intent revision (and worktree where applicable).
- Release claims when work completes, is safely preempted, is superseded, or is abandoned.
- Terminal cleanup may stop disposable sessions and remove temporary worktrees/branches only after proving that no uncommitted/unpushed work, unresolved evidence, or needed PR/branch state will be lost.
- Dirty, ambiguous, unpushed, or externally referenced state must never be auto-deleted merely to reclaim resources.

## Mutable evidence revalidation

Evidence tied to immutable source identity may be reused only for that identity. Mutable external state must be re-read at the decision boundary.

Before merge, deploy/release, external/prod mutation, or terminal PASS, refresh any materially relevant mutable state such as:
- PR review/approval and mergeability;
- CI/check status;
- issue/Work Packet eligibility and intent revision;
- deployment/runtime health;
- external resource existence/version/state.

Prefer subject/version identity or exact revision over arbitrary time-to-live. Historical “was green” evidence is not current authority when the external state can change independently.

## Coordinator / worker execution model

The Work Packet/objective is durable; coding-agent sessions are disposable workers.

A coordinator or equivalent outer loop should, when automation exists:

- select only dependency-eligible ACTIVE work;
- apply Work Packet sizing (`tools/work_admission.py size`) and WIP/resource admission (`tools/work_admission.py admit`) before starting a worker;
- keep separate worktrees/state ownership for concurrent workers;
- reconcile actual Git/PR/CI/runtime state after coordinator or worker restart;
- distinguish transient retry from semantic re-plan;
- restart or replace a crashed/stalled worker without inventing new scope;
- preserve terminal evidence and hand human-required decisions to the owner.

Do not encode a brittle micro-step state machine that requires one agent session to survive the whole workstream. The same outcome may span multiple fresh sessions when context/resource boundaries require it.

## Pure coordinator planner

The coordinator core is a pure function from structured facts to one bounded next action. It does not launch workers, poll, mutate GitHub, merge, send notifications, or stop sessions.

```bash
python3 tools/coordinator.py plan --facts <facts.json>
```

Contract:

- the same facts always emit the same decision;
- unknown or missing observational facts stay UNKNOWN and cannot satisfy a PASS, ALLOW, or exact-head gate;
- only `STATUS=ACTIVE` with complete dependencies, admission `ALLOW`, and resource `PASS` or `WARN` can emit `ADMIT_IMPLEMENTATION`;
- `PAUSED` emits `NOOP_PAUSED`, `BLOCKED` emits `BLOCK_HUMAN`, and `COMPLETE` emits `NOOP_COMPLETE`; none of those launch a worker;
- `PRIORITY` is scheduling evidence only; `CHANGE_RISK` changes verification depth, and HIGH or CRITICAL `MERGE_READY` requires coordinator audit PASS for the current intent revision;
- a worker whose starting intent revision differs from the packet emits `STALE_WORKER` and must not finalize or publish;
- `RESUME_WORKER` requires `progress_evidence=true`. A matching active worker without that evidence emits `WAIT_EXTERNAL`. Process liveness is not progress, and the planner does not invent elapsed stall time;
- resource `BLOCK` emits `YIELD_RESOURCE` and never stops or mutates unrelated sessions;
- ambiguous mutation facts emit `RECONCILE_AMBIGUOUS` before any retry;
- semantic failure emits `REPLAN_SEMANTIC_FAILURE` instead of repeating the same attempt;
- CI and review gates bind to the exact pull request head; stale or unknown CI cannot merge;
- notification intent uses `repository|workstream|intent_revision|decision_class|subject_version` and suppresses identical WAIT repeats and worker resume micro-steps;
- org rollout or canary authorization stays a separate gate and is not implied by merge readiness;
- the planner performs no model call, process spawn, GitHub mutation, merge, Telegram send, or schedule.

`plan` returns exit 0 when it emits a decision. Exit 3 is reserved for malformed or execution-keyed facts. The decision schema is `schemas/coordinator-decision.schema.json`.

## Bounded coordinator watch

The watch evaluator is a pure function from structured facts plus durable watch state to one re-entry result. It calls the planner. It does not poll, spawn processes, call GitHub, merge, send notifications, or start/stop Cursor. Host scheduling and delivery are a separate later adapter.

```bash
python3 tools/coordinator_watch.py evaluate --facts <facts.json> --watch-state <state.json>
```

Watch classes are `work_packet_state`, `exact_head_ci`, `review_state`, `worker_progress_or_yield`, and `resource_admission`. Identity is `repository + workstream + intent_revision + watch_class + subject_version`.

Contract:

- the same facts and watch state always emit the same result;
- identical CI pending observations emit `RECHECK_LATER` with notification suppressed and a bounded backoff timestamp;
- an exact-head CI or review transition that changes the planner decision emits `WAKE_COORDINATOR`;
- CI PASS bound to a different head does not wake merge;
- `RESUME_ADMITTED_WORKER` requires planner `RESUME_WORKER`, watch class `worker_progress_or_yield`, `progress_evidence=true`, the same intent revision, resource `PASS` or `WARN`, and admission `ALLOW`; any other watch class fails closed with `BLOCK_RECONCILIATION` and does not resume a worker;
- resource `BLOCK` or WIP admission `DENY` does not resume a worker and does not mutate unrelated sessions;
- a changed intent revision or subject version closes the stale watch and does not act;
- caller `watch.subject_version` is accepted only when it equals the coordinator-derived subject; a mismatch is rejected and is not persisted;
- an observation older than the latest accepted observation (`last_observation_at`, or `last_transition_at` when schema-v1 state omits that field) emits `NO_CHANGE` and does not change any durable watch state; an equal or newer timestamp is accepted and advances `last_observation_at`;
- `consecutive_transient_failures` is the authoritative transient retry count and exhausts at `wait.retry_budget` into `BLOCK_HUMAN` / `NOTIFY_OWNER`;
- notification keys are `repository|workstream|intent_revision|result|coordinator_decision|subject_version`, so distinct coordinator decisions do not share one wake key;
- ambiguous mutation facts emit `BLOCK_RECONCILIATION` and do not retry;
- `BLOCK_HUMAN` emits `NOTIFY_OWNER` once; an identical owner-notify key is deduplicated;
- `COMPLETE` emits `CLOSE_WATCH`;
- malformed facts, unknown keys, and execution keys fail closed;
- the evaluator performs no subprocess, network, GitHub mutation, merge, Telegram send, or session stop.

`evaluate` returns exit 0 when it emits a result. Exit 3 is reserved for malformed or execution-keyed facts. The result schema is `schemas/coordinator-watch.schema.json`.

## Coordinator watch host

The host is a run-once adapter around the watch evaluator. An external scheduler owns cadence. The host does not sleep, busy-loop, or keep a coding agent alive to poll.

```bash
python3 tools/coordinator_watch_host.py run-once --request <request.json>
```

One request declares one watch class, repository, workstream, and effect target. Mutable lock, watch-state, ledger, and journal paths are derived from that identity. `work_budget` must be 1.

Contract:

- a single-instance lock is acquired before evaluation; if the lock is already owned, the run yields and does not evaluate or deliver;
- fresh authoritative facts are collected read-only for the declared watch; caller command, shell, endpoint, or URL fields fail closed;
- the host calls `coordinator_watch.py` and does not reimplement its decisions;
- `NO_CHANGE`, `RECHECK_LATER`, and `CLOSE_WATCH` persist bounded watch state and deliver no typed action;
- `WAKE_COORDINATOR` and `RESUME_ADMITTED_WORKER` are authorized only when `worker_adapter.py` verifies the exact host-built effect without consuming the dispatch, then delivered only when `send_effect` returns a durable GitHub comment receipt; authorization alone is not delivery;
- `NOTIFY_OWNER` uses that same unconsumed dispatch check, then one bounded `INFO` Telegram send to `api.telegram.org`; `NOTIFICATION_DELIVERY=VERIFIED` requires a durable message receipt. `COMPLETE` is reserved for whole Work Packet completion and is not emitted by a watch pass;
- before send, the host atomically reserves the dispatch for that effect digest and executor attempt. Another effect or attempt cannot send it. Final consumption happens only after the receipt is durable. A crash before send stays retryable by the same attempt. A crash after the send marker without a receipt is reconcile-blocked and is not retried;
- a changed intent revision or subject, a resource `BLOCK`, or an admission `DENY` before delivery yields zero actions and does not stop or mutate unrelated sessions;
- an ambiguous prior action outcome blocks retry; the same applied key or a consumed dispatch is deduplicated;
- missing or invalid trusted dispatch denies the external or worker effect;
- persisted watch state and the action ledger store bounded metadata only;
- lock, watch-state, ledger, and effect-journal paths are derived from the watch identity under the host state root. Caller filenames that differ are rejected. Symlink and path escape fail closed before mutation;
- authoritative packet, branch, subject, and CI facts come from `collect_authoritative` through fixed `/usr/bin/gh` provenance, not caller `PATH`. Git dirty/unpushed and worker liveness/progress are used only when the pinned worktree and session are that authoritative branch at its exact HEAD; a mismatch stays unobserved and is not attributed to the current packet. Resource and admission come from trusted machine observation or stay `UNKNOWN`. Work Packet text cannot set those passing values, and a session does not receive `starting_intent_revision` unless a claim independently binds the same repository, workstream, and revision. Caller-selected fact files and the request branch cannot supply both sides of an authority comparison;
- the host performs no merge, caller-selected command, session stop, or model call. GitHub delivery is one fixed `gh api` issue comment. Owner delivery is one fixed Telegram INFO send.

`run-once` returns exit 0 when it emits a result. Exit 3 is reserved for malformed or execution-keyed requests. The result schema is `schemas/coordinator-watch-host.schema.json`.

## Trusted worker external-write adapter

The planner stays decision-only. External Issue/PR writes and publication effects go through the trusted adapter, which authorizes at most one typed effect and does not itself perform the mutation:

```bash
python3 tools/worker_adapter.py evaluate --request-json <facts.json>
```

Immediately before a write, re-read the authoritative Work Packet and compare it with the bound `target_repo`, `workstream`, `intent_revision`, `subject_head`, `branch`, and `requested_action`. The request digest also covers the concrete mutation target and content. A trusted effect is accepted only when `skills-contract` `authorize` verifies host-signed binding and dispatch assertions for that exact digest and `network.post` / `external_write`. Caller-supplied permission, `trusted_dispatch.result=PASS`, or a matching hash without that verification cannot authorize a write. A missing host trust boundary fails closed and does not authorize. `author_association` does not authorize.

A revision, target, or head mismatch returns `STALE_WORKER` and authorizes zero writes, including the case where a worker prepared an Issue body under an older intent revision. An unknown mutation result returns `RECONCILE_AMBIGUOUS` and must not be retried blindly. The same applied digest returns `NO_CHANGE`. Resource `BLOCK` returns `RESOURCE_BLOCKED` and never stops unrelated sessions. The result schema is `schemas/worker-adapter-result.schema.json`.

## Human-attention and notification budget

Human attention is a constrained engineering resource.

- Notify on meaningful Work Packet transitions, terminal outcomes, or a decision/action that actually requires the owner.
- Do not notify for every micro-edit, test invocation, short agent session, or intermediate subtask completion.
- Deduplicate/coalesce repeated notifications for the same Work Packet and state.
- A worker-level “COMPLETE” signal is handoff evidence only; it is not packet completion authority.
- Quiet/no-change polling or automation runs should remain quiet unless an actionable condition appears.

## Trusted Work Packet provenance

An executable AI Work Packet must come from authenticated repository-scoped GitHub state for the resolved `TARGET_REPO`.

Trusted sources:

- authenticated `gh` against the exact origin repository
- an available authenticated GitHub integration bound to that same repository

Trusted Work Packet Issue authors are limited by effective repository permission, not `author_association`:

1. Resolve the Issue author login from authenticated Issue state.
2. Query authenticated `repos/{owner}/{repo}/collaborators/{author}/permission` (or the equivalent GitHub integration permission lookup).
3. Accept only effective permissions `write`, `maintain`, or `admin`.
4. Fail closed on API failure, missing/unknown permission, or any weaker permission with `WORK_PACKET_AUTHOR_UNTRUSTED`.

`author_association` may be recorded as evidence but MUST NOT authorize execution. Association values such as `OWNER`, `MEMBER`, or `COLLABORATOR` are insufficient when effective permission is only `read`, `triage`, or otherwise weaker than `write`.

Untrusted sources for execution:

- pasted Issue bodies
- conversation history alone
- unauthenticated web scrapes or mirrors
- reconstructed packet text from memory or another repository

If authenticated packet access is unavailable, stop with `WORK_PACKET_PROVENANCE_UNTRUSTED` rather than executing untrusted copies.

## ChatGPT behavior

When the user asks to continue/resume an existing engineering workstream:

- resolve the target repository
- load its active Work Packet
- synchronize the packet with the owner's latest explicit request before implementation handoff
- verify `TASK_KIND` / `OWNER_INTENT` / `Next Action` coherence when packet v2 is used
- verify current GitHub/repository facts
- continue from `Next Action` only when it still matches the current owner intent
- do not ask the user to paste prior chat unless the required durable state genuinely does not exist

## Cursor behavior

Repository adoption should provide `.cursor/commands/resume.md`. Managed upgrades must keep that adapter synchronized with the canonical template. When a known local alias such as `.cursor/commands/work-resume.md` is already present or explicitly managed, keep it synchronized to the same canonical resume text.

The resume command:

- requires a working local shell/process and Git context for repository implementation; if these cannot start, reports `ENVIRONMENT_BLOCKER` instead of probing unrelated knowledge systems
- derives repository/branch/HEAD from Git
- loads the repository-scoped active Work Packet only through an available GitHub integration or authenticated `gh` for the resolved origin repository
- rejects pasted, conversational, or otherwise untrusted packet copies with `WORK_PACKET_PROVENANCE_UNTRUSTED`
- rejects Work Packet Issues whose author lacks effective `write`/`maintain`/`admin` permission with `WORK_PACKET_AUTHOR_UNTRUSTED`; `author_association` MUST NOT authorize execution
- fails closed on incomplete adopted-project context unless `TASK_KIND=ADOPTION`
- fails closed on missing/ambiguous packets, invalid status values, or material owner-intent/Next-Action mismatch
- reads only task-relevant canonical references with minimum sufficient reasoning/context
- executes the current bounded local/deterministic phase beginning at `Next Action`
- updates the same packet with concise verified state/evidence after meaningful milestones
- yields instead of polling when CI/review/deployment or another machine-observable external condition is pending; coordinator/automation owns waiting and re-entry
- uses BLOCKED only for human/external actions that cannot be resolved by machine-observable re-entry

The default remains single-agent sequential execution when the owner's project rules require it. A long-lived coding-agent session is not a substitute for durable packet state or external orchestration.

## Persistent-session resource guard

Before any new `agent persist` session, run the canonical preflight:

```bash
python3 tools/cursor-resource-preflight.py
```

Host policy may override thresholds without editing a repository, using `ENGINEERING_SYSTEM_CURSOR_RESOURCE_GUARD` or `~/.config/engineering-system/cursor-resource-guard.yaml` (then `/etc/engineering-system/cursor-resource-guard.yaml`). Exit 0 is `PASS` or `WARN` and may proceed. Exit 2 blocks on memory, swap, or session pressure. Exit 3 blocks because memory facts, `agent persist list`, or the override could not be trusted. Neither blocking result may stop or mutate existing Cursor sessions. Unsupported platforms report `BLOCK` instead of guessing. The always-applied Cursor rule stays small; this tool and this standard hold the procedure.

## Parallel-work admission and WIP ownership

Default execution remains sequential. A second worker may start only when a coordinator proves independence from packet/claim/worktree/resource facts.

Use the canonical oracle:

```bash
python3 tools/work_admission.py admit --request-json <facts.json>
python3 tools/work_admission.py size --request-json <sizing.json>
python3 tools/work_admission.py release --request-json <release.json>
```

Admission identity is `repository + workstream + intent_revision`, scoped to a dedicated worktree when concurrent. Machine-readable claims must record owned paths and any shared runtime id with an explicit isolation flag.

`admit` returns `ALLOW` only when all of the following hold:

- host resource preflight facts are `PASS` or `WARN` (never invent capacity by stopping unrelated sessions);
- active claim count is below the configured WIP limit (default `1`, so parallelism is off unless raised);
- no shared worktree with an active claim;
- no overlapping claim identity or conflicting intent revision on the same workstream;
- no overlapping owned paths;
- no shared mutable runtime unless every concurrent claimant marks that runtime isolated;
- no ambiguous proposed or active claim.

Otherwise `admit` returns `DENY` with an explicit class such as `WIP_LIMIT`, `SHARED_WORKTREE`, `OVERLAPPING_CLAIM`, `STALE_INTENT_REVISION`, `OVERLAPPING_PATHS`, `SHARED_RUNTIME`, `HOST_BUDGET`, or `AMBIGUOUS_CLAIM`. Exit `3` is reserved for malformed/untrusted facts.

`size` emits deterministic `BATCH` / `KEEP` / `SPLIT` from structured sizing signals (primary outcome count, adjacent shared oracle, unrelated domains/gates/rollback, effort band, and context budget). File/LOC/Issue counts are inputs only when encoded as those signals; they are not the sizing authority.

`release` reconciles ownership safely:

- `release-claim` allows COMPLETE / ABANDON / SUPERSEDED / RELEASED claims;
- `cleanup-worktree` additionally refuses dirty, unpushed, or ambiguous state;
- neither action may stop or mutate unrelated Cursor sessions to reclaim capacity.

## Independent verifier for terminal evidence

For risk-appropriate terminal PASS, a coordinator evaluates structured evidence with a provider-neutral verifier. The implementer's self-report is never the completion oracle by itself.

```bash
python3 tools/independent_verifier.py verify --request-json <evidence.json>
```

Contract:

- bind verification to an exact 40-char subject HEAD; different-HEAD or missing subject identity fails closed;
- `CHANGE_RISK=HIGH|CRITICAL` requires a verifier actor whose `identity` and `context_id` are both distinct from the implementer;
- `CHANGE_RISK=LOW|MEDIUM` stay aligned with `standards/QUALITY.md` verification depth (targeted/affected evidence and review); a distinct fresh-context verifier actor is not mandatory at those depths;
- every declared completion-oracle evidence record must be `PASS` on the same subject HEAD; `FAIL` / `BLOCK` / `NOT_RUN` / unexecuted cannot be promoted;
- actionable review findings must be `FIXED`, `EVIDENCE_DISPOSITION`, or `NOT_ACTIONABLE`;
- HIGH/CRITICAL require mutable CI/review/runtime evidence that carries current `subject_id` + `version_id` (not a historical generic PASS);
- request `expected_mutable` declares the required subject/version contract; each mutable evidence item must match it, and CI `version_id` must equal the subject HEAD (stale/unrelated CI DENYs as `STALE_MUTABLE`);
- CRITICAL additionally requires present human approval when marked required;
- the verifier evaluates JSON facts only and refuses request keys that imply command execution (`command`, `shell`, `argv`, `execute`, …).

`verify` returns `PASS` or `DENY` with an explicit class such as `SAME_ACTOR`, `VERIFIER_REQUIRED`, `HEAD_MISMATCH`, `ORACLE_NOT_PASS`, `REVIEW_OPEN`, `MUTABLE_MISSING`, `STALE_MUTABLE`, `HUMAN_APPROVAL_MISSING`, or `EXECUTION_FORBIDDEN`. Exit `3` is reserved for malformed/untrusted facts.

## Efficiency telemetry and task budget

P0b records verified exact-head outcomes against cost, time, rework, and human intervention. Collection is provider-neutral, local, and off unless a repository writes a record.

- Persist only a generated run ID, repo, workstream, task kind, the session profile, timestamps, exposed usage fields, counts, validation IDs, exact-head evidence, and terminal PASS, BLOCK, or FAIL.
- Do not persist prompts, conversation, source, tool payloads, secrets, logs, or absolute local paths. Additional fields fail closed.
- Missing provider usage, cache, or cost stays null. Do not estimate.
- Default output is `engineering-system/telemetry/` under the repository's absolute Git directory (`git rev-parse --absolute-git-dir`). That location is Git metadata, so generated records stay outside the tracked worktree for canonical repositories, adopted repositories, and linked worktrees. The directory is bounded to 32 records and easy to disable with a `DISABLED` marker. `.cursorignore` and a textual `.gitignore` rule are not the retention boundary. There is no automatic network export.
- Capture provider, model, reasoning, and toolset at session start. A later change requires a recorded justification. Do not switch profiles silently.
- Soft task budgets are optional. Exhaustion yields terminal `BLOCK` with disposition `YIELD`. Further retries fail closed.

## GitHub marker and lifecycle

Canonical Issue title prefix:

```text
[AI Work]
```

An `ai-work` label is optional. The title prefix plus required identity fields are the portable deterministic markers.

Use only:

```text
ACTIVE   -> open Issue
PAUSED   -> open Issue
BLOCKED  -> open Issue
COMPLETE -> close Issue
```

Do not introduce tool-specific lifecycle states. Tool readiness, CI waiting, or implementation phases belong in `Current State`, `TASK_KIND`, `OWNER_INTENT`, and `Latest Evidence`.

A new independent workstream gets a new Issue rather than reusing an unrelated completed packet.

## Evidence and authority

Authority remains:

1. actual code/config/runtime and immutable Git state
2. canonical product/specification artifacts
3. repository engineering metadata
4. executed deterministic evidence
5. AI Work Packet as current coordination state
6. conversation history

The Work Packet never overrides code, tests, canonical specifications, Git state, or release evidence.

## Adoption requirements

For repositories using session continuity:

- add the AI Work Packet Issue template
- preserve the canonical `[AI Work]` title prefix
- add Cursor `/resume` command when Cursor is used
- ensure ChatGPT/Cursor adapters know to resolve repository first
- never store secrets in Work Packets
- never use a Work Packet update as release evidence
