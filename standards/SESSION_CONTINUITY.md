# AI Session Continuity & Work Packet Standard

## Purpose

Long-running AI-assisted engineering must not depend on replaying or copying old assistant conversations.

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

A GitHub Issue is coordination state, not automatically the unit of implementation execution. Do **not** turn one tiny Issue/finding into a separate worker cycle by default, and do not combine unrelated outcomes into one oversized execution cycle.

Before every implementation handoff, the coordinator must classify the next executable scope as:

- `BATCH` — too small by itself. Combine adjacent small Issues/findings when they share the same repository area, implementation context, owner intent, and validation oracle. Multiple GitHub Issues may be referenced by one medium-sized Work Packet/Next Action.
- `KEEP` — right-sized. Prefer one primary independently verifiable outcome with a few tightly coupled subgoals that one implementation context can implement, test, and hand back while retaining enough context for deterministic validation.
- `SPLIT` — too large. Split when the scope contains multiple independently releasable outcomes, unrelated domains/owners, materially different approval or validation gates, unclear rollback boundaries, or is likely to exhaust the session context before implementation **and** validation finish.

Default handoff behavior:

1. Do not use “one GitHub Issue = one implementation job” as a rule.
2. Batch micro-fixes and closely related findings into a coherent medium-sized packet instead of creating repeated short implementation cycles and notifications.
3. Keep one primary outcome; a small number of tightly coupled subgoals is preferred over either a single trivial edit or a broad multi-domain program.
4. File count and LOC are advisory only. Structural coupling, independent verification, approval boundaries, rollback boundaries, and context budget determine size.
5. Reserve context for implementation **and** testing/review. If implementation alone is expected to consume the reliable session context, split before handoff.
6. If scope materially expands, update or split the Work Packet within the current owner scope and continue the coherent outcome. A separate coordinator or new owner confirmation is needed only for a genuine scope/authority decision, not routine re-sizing.
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
- During execution, if context usage is already high and a distinct implementation subgoal remains, persist the verified state and continue that subgoal after a context reset on the same reusable project persistent session rather than forcing the original conversation through compaction/noise. `/clear` resets conversational context only. It does not create a persistent worker and does not discard Git or worktree truth. Finishing the current tightly coupled validation step is preferred over splitting in the middle of an atomic check.

These values are calibration defaults, not universal constants. P0/P1 efficiency telemetry should measure rework, validation failures, context pressure, and handoff overhead; revise the band from observed outcomes rather than increasing task size merely because a model can technically run longer.

## Required identity fields

Every packet body begins with:

```text
PACKET_VERSION=3
TARGET_REPO=owner/repository
WORKSTREAM=<stable-slug>
STATUS=ACTIVE|PAUSED|BLOCKED|COMPLETE
BRANCH=<branch-name|N/A>
TASK_KIND=DESIGN|DEVELOPMENT|TEST|REVIEW|RELEASE|OPERATIONS|ADOPTION|DOCUMENTATION|CLEANUP|MIXED
OWNER_INTENT=<one concise line describing the owner's current explicit request>
LAST_VERIFIED_HEAD=<40-char-sha|UNKNOWN>
INTENT_REVISION=1
CHANGE_RISK=MEDIUM
EXECUTION_PROFILE=<selected-profile-id>
EXECUTION_PROFILE_REVISION=<selected-profile-revision>
```

`LAST_VERIFIED_HEAD` is evidence of the last observed state, not authority. The current repository state must be re-verified on resume.

`TASK_KIND` and `OWNER_INTENT` describe the current bounded handoff, not the lifetime purpose of the workstream. Refresh them whenever the owner's explicit request changes materially.


### Optional coordinator fields

`PRIORITY` is optional scheduling metadata:

```text
PRIORITY=NORMAL
```

- `PRIORITY=URGENT|HIGH|NORMAL|LOW` controls scheduling order only. It is not a security/risk rating.
- Packet v3 requires `INTENT_REVISION`, `CHANGE_RISK`, `EXECUTION_PROFILE`, and `EXECUTION_PROFILE_REVISION` in the required identity block above.
- `INTENT_REVISION` is a monotonically increasing integer for material handoff changes.
- `CHANGE_RISK=LOW|MEDIUM|HIGH|CRITICAL` controls verification/approval depth, not scheduling priority.

Legacy v2 packets remain bounded compatibility inputs when accepted by the current execution profile. Packet v1 or versionless packets are not runnable under profile-v3. Do not create new v1/v2 packets. Migrate an accepted legacy v2 packet to v3 profile identity/revision on the next meaningful packet update; missing `PRIORITY` may still be treated as `NORMAL`.

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

Keep tangential discoveries out of the active scope unless the coordinator explicitly re-sizes the packet. Record a separate follow-up Issue/Work Packet only when the discovery is a genuinely independent durable scheduling outcome: it has a different owner/approval/rollback boundary, a separate completion oracle, or cannot safely fit the current bounded outcome after sizing. Findings, bugs, review comments, and remediation steps required by the current outcome stay in the current packet and are batch-remediated there; do not create one Issue per finding or micro-step. When a related follow-up already exists, update/reuse it instead of creating a duplicate. Do not silently absorb unrelated work merely because it was discovered nearby.

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
6. Preemption must preserve recoverable state: finish/abort the current atomic operation safely, persist current evidence, set the packet to PAUSED whenever repository-level scheduling should move to another workstream, including CI/review/dependency waits. A machine-observable watcher/worker may retain its own wait state for automatic re-entry, but that wait does not make the Work Packet itself ACTIVE. Release worker claims when safe. Do not terminate in the middle of an irreversible/external mutation merely to switch tasks.
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

### Research-informed continuation

A research/discovery phase is part of the engineering workstream, not a terminal advisory handoff. When competitive/vendor/best-practice/standards research materially resolves architecture, public-contract, dependency, migration, or product-direction uncertainty:

- if the owner accepts the result or says `proceed`, `continue`, `진행해`, `계속`, or an equivalent instruction, treat that as authority to perform the minimum durable design/roadmap/Work-Packet updates and then continue into the already-scoped implementation;
- do not require the owner to separately say “write the docs” and then “start coding” when those are the natural next steps of the accepted workstream;
- persist accepted conclusions before code when they create or change a durable architecture/public-contract/dependency boundary;
- use any configured derived-context adapter only as optional context/history, never as the canonical implementation contract;
- stop for owner input only when research exposes a genuinely unresolved choice that changes breaking compatibility, security/privilege, irreversible publication/destruction, production behavior, credentials, legal/distribution authority, or another explicit approval boundary.

The required sequence for material research-informed work is therefore:

```text
research/discovery when warranted
 -> accepted decision
 -> minimum canonical documentation / roadmap synchronization
 -> implementation
 -> deterministic validation
 -> PR/merge/closure by the normal risk contract
```

`STATUS` is deliberately small and fixed. Do not invent transient readiness values such as `WAITING` or `DONE`.

- `ACTIVE` — work is runnable now: dependencies and required execution environment are available and its `Next Action` can make measurable progress without waiting for another condition. Machine-observable CI/review/dependency waits belong to watcher/worker state, not packet `ACTIVE`.
- `PAUSED` — execution is intentionally deferred, including repository-level scheduling waits such as CI, review, or incomplete dependencies. PAUSED does not imply cancellation or owner intervention.
- `BLOCKED` — progress requires a human/external action or a required execution environment is unavailable.
- `COMPLETE` — terminal; no executable `Next Action` remains.

"Ready for implementation" is represented by `STATUS=ACTIVE` plus a valid `Next Action`, not by a new status value.

Packet version 2 is legacy-compatible only through the current execution profile. Packet v1 and versionless packets are non-runnable and must be migrated before execution. Agents may resume a valid v2 packet only when profile compatibility accepts it, and must migrate it to packet v3 profile identity/revision on the next meaningful packet update. New packets use v3; legacy compatibility is not a template or steady-state authoring path.

## What must not be copied into a Work Packet

Do not paste:

- old conversations
- previous handoff prompts
- previous assistant prompts
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
- after every ACTIVE packet create or material update, lint the exact resulting authoritative body with `python3 tools/context_epoch.py packet-lint --body-file <file> --expect-target-repo <bound-owner/repo>` using the owner/project-bound expected repository; a `TARGET_REPO_SCOPE_MISMATCH` or other BLOCK means the packet is non-runnable and must be corrected before implementation, handoff, or any implementation session/process start or resume

Do not keep accumulating old phase text in the Issue body.

History already exists in Git commits, PRs, CI, Issue edits/comments, and closed Issues.

### Bounded Work Packet projection

Routine resume must not copy an append-only Issue body into model context. After authenticated GitHub retrieval and author-permission verification, pipe the selected body through `tools/context_epoch.py packet-project` and use that bounded current-state projection for ordinary execution.

`tools/context_epoch.py` is a managed provider-neutral adoption helper. Bootstrap and managed upgrade install the byte-identical canonical helper; a missing copy makes the adopted execution surface incomplete and must fail closed rather than falling back to the raw Issue body. Bootstrap and managed upgrade also reject an incompatible pre-existing helper before mutation.

Candidate selection and routine projection are two separate authenticated reads, so the second read must be bound to both the structural values and the content digest emitted by `packet-identity`. `packet-project` validates `PACKET_VERSION`, `TARGET_REPO`, `WORKSTREAM`, `STATUS`, `BRANCH`, `TASK_KIND`, `INTENT_REVISION`, and `PACKET_BODY_SHA256` against the exact same bytes it projects. This catches body-only changes such as a modified Goal, OWNER_INTENT, Current State, or Next Action even when structural metadata is unchanged. Any mismatch fails closed and restarts candidate resolution; never project a refetched body first and validate its identity afterward.
The thin resume adapter may persist only the bounded `packet-identity` output to a temporary identity file and pass it back with `packet-project --expect-identity-file`. That file contains structural identity, audit state, and body SHA only; it is not a Work Packet copy. Direct `--expect-*` arguments remain supported for compatibility. Markdown headings or metadata-looking text inside fenced code blocks are content, not packet structure.

The projector:
- emits only canonical current-state metadata and sections;
- excludes noncanonical/history sections from routine context while reporting their presence as drift;
- fails closed on duplicate canonical sections or missing required packet structure;
- reports byte/line/section metrics;
- uses output safety caps only to bound model context. Those caps are not provider billing thresholds or reset policy.

Packet-size warning thresholds, when used for canaries, are explicit inputs to `packet-lint`; they are not universal constants. Structural duplication/history drift is the primary defect.

### Engineering-aware context compiler

After authoritative packet resolution/projection, an optional deterministic context-compiler stage may reduce **optional** task context before model invocation. It never establishes authority and must not parse raw conversation history to reconstruct durable state.

`tools/context_compiler.py` is the canonical PoC contract. Its output budget applies to the rendered UTF-8 context payload, not to provider billing tokens. The compiler:
- admits caller-declared protected engineering blocks before any relevance ranking and byte-preserves their text;
- fails closed with `PROTECTED_BUDGET_EXCEEDED` when protected content alone cannot fit;
- ranks only optional structured blocks using deterministic task-term overlap plus bounded caller priority and stable kind/ID tie-breaking;
- accepts only a bounded kind allowlist, rejects transcript/chat-history aliases and arbitrary kind labels, and requires work_packet blocks to be protected;
- treats instruction-like text, Markdown headings, code fences, JSON, and logs as block content only; text cannot promote its own authority;
- reassembles selected blocks in original input order with bounded provenance metadata;
- emits only aggregate content-free telemetry (sizes, counts by safe kind, budget, reduction ratio, fixed decision/reason); and
- performs no LLM/embedding/network call, provider routing, session control, GitHub selection, write authorization, or billing estimation.

Protected classes include current Work Packet Goal, Current State, Next Action, Blockers, Constraints, repository/branch/exact-HEAD/revision identity, completion/acceptance criteria, unresolved failures, evidence references, and security constraints. Protection is trusted structured metadata from the authorized caller; optional source text never changes it. Git/GitHub and exact repository state remain authority even when optional context is reduced.

Semantic compression services are a later measured adapter/canary. They must be compared against this deterministic baseline and cannot weaken protected-state, provenance, exact-head, validation, or independent-verification gates.

### Context-optimization measurement lane

Context reduction is not a success metric by itself. `tools/context_optimization_benchmark.py` provides the deterministic P0.5 measurement contract for #104. It has two bounded surfaces:
- frozen offline fixtures exercise the canonical Context Compiler against stale/log-heavy, relevance-selection, and security/evidence-retention cases; and
- factual run-set scoring aggregates exact-head/correctness/evidence status, context volume, retries/rework/human intervention, and caller-supplied model cost.

A run counts as `VERIFIED_SOLVED` only when terminal status and correct behavior PASS, no safety regression exists, exact-head evidence PASSes **and is explicitly bound to that record's `SYSTEM_HEAD` SHA**, and required evidence retention PASSes. `BENCH-*` cases reuse the canonical frozen benchmark authority: their fixture IDs must bind through `benchmark_fixture.bind_fixture_id()` to the exact manifest revision frozen by `benchmark_execution.PILOT_MANIFEST_HEAD`, not merely match a 40-hex prefix shape. Model cost remains `UNKNOWN` if any record in an arm lacks a measured numeric value. `cost_per_verified_solved_task` is emitted only when the entire arm has measured model cost and at least one verified solved task; bytes are never converted into provider tokens or cost.

One arm represents one exact system HEAD, one arm/case pair may appear only once, every arm in a scored comparison must cover the same case set, and the same case ID must bind to the same frozen fixture ID across arms. Reports are ordered facts only: they do not emit a winner, ranking, weighted score, or aggregate quality score. Promotion decisions remain outside this helper and must apply the #104 correctness, privacy, cache, and economics gates.

The measurement helper is offline/read-only: it launches no worker, calls no provider/model/network service, mutates no GitHub/runtime state, and retains no raw context text, prompt, absolute path, credential, or tool output in its reports.

Before a real provider/model A/B result is treated as economically comparable, `tools/context_canary_gate.py` must bind every #106 arm/case record to exactly one existing efficiency-telemetry record. #106 run records remain backward-compatible, but live-canary records carry an optional `TELEMETRY_RUN_ID` that the live gate requires and must match the validated telemetry `run_id` exactly; caller-supplied arm/case wrappers alone are never binding authority. The gate also requires one known provider/model/reasoning/toolset profile with no switches, one Engineering System HEAD across all arms, EXACT_HEAD/PASS telemetry bound to each run HEAD, complete provider-exposed input/output/cache-read/cache-write/cost fields, and exact agreement between #106 measured model cost and telemetry cost. It preserves #106 same-case-set and verified-solved requirements. Any mismatch or unknown fact blocks canary eligibility; the report is factual only and never selects, ranks, or recommends an optimizer. After live-canary comparability passes, `tools/context_shadow_gate.py` provides the strict P0.75-B shadow action-equivalence gate. It consumes caller-supplied bounded observations only and performs no provider execution. Every observation must bind to the authoritative arm/case plus `TELEMETRY_RUN_ID`, contain only the bounded material action/target vocabulary, terminate at `COMPLETE`, and match the selected control arm's ordered material trace exactly for the same case. Missing, duplicated, swapped, non-terminal, or divergent observations fail closed; bounded `BLOCK` and `WAIT` actions remain valid material facts when both arms follow the same trace and ultimately reach `COMPLETE`. The report emits only bounded factual counts and `EQUIVALENT`; it exposes no raw traces and grants no optimizer-promotion, merge, release, deployment, or policy authority. Probabilistic/learned equivalence remains a separate later gate. Learned/full-request compression remains research-only unless a candidate fits the approved provider workflow without dedicated local model-serving infrastructure, has explicit privacy/egress approval, and passes the existing live-comparability and shadow-equivalence gates. Engineering System does not require Ollama, a local LLM, or a dedicated GPU/inference server.

The P0.75-C factual net-economics layer in tools/context_economics.py runs only after the live-comparability and shadow-equivalence gates. It uses the explicit control arm and emits only candidate-minus-control deltas for provider-measured cost, kept/original context bytes, input/output/cache-read/cache-write tokens, tool turns, retries/rereads/compactions, rework, and human interventions. Token classes remain separate because provider billing/cache semantics must not be inferred from arithmetic token totals. A zero-cost control yields no percentage delta. The report is EVIDENCE_ONLY, selects no optimizer, grants no routing/promotion/merge/release/deployment authority, and performs no provider/model/network/process execution.


### Reversible structural folding canary

`tools/context_fold.py` is an optional local-only primitive after deterministic context avoidance/selection and before any semantic compression. It is disabled unless the caller explicitly names optional Context Compiler block IDs to fold. Protected blocks, including `work_packet` state, fail closed and are never stored or replaced.

Folded UTF-8 bytes are retained only under the current worktree's absolute Git metadata directory in a private bounded store. The store creates a random local key, derives HMAC-SHA256 content handles from that key and exact bytes, uses private directory/file permissions, rejects symlink/out-of-bound/corrupt state, enforces entry/count/total-byte limits, and never evicts live entries merely to make room. Expansion authenticates the marker and keyed content address and returns exact original text or fails closed; explicit purge removes retained entries.

The model-visible marker contains only version, store-local opaque handle, and original byte length. Fold telemetry is content-free aggregate data only and never includes raw text, reference/path, handle, digest, secret, or credential. Disabled mode writes no store state and preserves the input structure. This primitive performs no model/embedding/network call, does not change Context Compiler selection semantics, is not enabled by default, and does not claim provider token, cache, billing, or solved-task improvement from byte reduction alone. Promotion requires the #104/#106 measured canary gates.



## Direct execution and coordination repair

The Engineering System exists to help one capable agent ship correct work using durable repository state and evidence. It is not a second implementer or an approval queue for ordinary engineering. Packet sizing, context orientation, coordinator planning/watches, admission and derived context are tools; invoke them when they resolve a concrete need, rather than executing every tool as a resume checklist.

`work_admission.py eligible --request-json <facts.json>` consumes the fresh authoritative packet body (`body`), local execution-profile root (`profile_root`), owner-bound `expected_target_repo`, observed checkout `observed_head` (full SHA), `observed_branch` and absolute `observed_worktree` matching `profile_root`, observed `issue_state` (OPEN/CLOSED), `dependencies_ready` boolean and `waiting_for` array. It is a pure selection check, not execution authority or a live GitHub fetch. Re-fetch facts at selection and before mutable external writes. Optional orientation/coordinator tools do not gate an already verified ordinary command.

The current explicit owner instruction is the first execution authority. A missing, stale or contradictory packet is repairable coordination data when the owner has clearly authorized the work. Reconcile it from current Git/GitHub/roadmap facts, lint the resulting body, then execute. Do not fabricate authority, bypass production/destructive/credential/permission/release boundaries, or modify unrelated dirty worktrees.

Packet completion and repository-level continuation have different finish lines. Completing one packet closes that bounded workstream; it immediately returns the same runtime to the authorized roadmap. A CI wait pauses the affected lane and permits independent work. Record the concrete condition in Latest Evidence, for example `WAITING_FOR_CI=<subject>`, and keep that waiting lane PAUSED. Clear the marker or record a terminal value such as `WAITING_FOR_CI=PASS` when readiness is observed; selection and handoff share this pending-condition check. Do not invent a new packet status. If no independent work remains, persist the concrete wait and finish honestly; do not invent tasks to keep a session alive.

## Deterministic packet resolution

When resuming work:

1. Resolve the target repository first from the owner's current explicit project/repository context, using the current Git remote only when that context does not already identify the repository, and bind it as the expected target for the current owner-selected workstream. Only a new explicit owner project/repository switch may replace the binding.
2. Once bound, do not search unrelated repositories. Cross-project handoffs, dependencies, Issue references, derived context, and waiting-work scheduling may be inspected as read-only context but never retarget implementation/testing/mutation authority.
3. Resolve the current branch when a local repository is available.
4. Read only open Issues whose title begins with `[AI Work]`. An `ai-work` label may be used as an optional search accelerator, but must not be required for correctness.
5. Require exact `TARGET_REPO` match and lint with `python3 tools/context_epoch.py packet-lint --expect-target-repo <bound-owner/repo>`; `TARGET_REPO_SCOPE_MISMATCH` is non-runnable.
6. Prefer an exact `BRANCH` match when branch context exists.
7. Select one runnable `STATUS=ACTIVE` packet for the current action, using owner priority, dependencies, PRIORITY and branch context. Multiple independent lanes may coexist. Use `python3 tools/work_admission.py eligible --request-json <facts.json>` with the payload described above; structural lint alone is not runnable eligibility.
8. For packet v3, require `TASK_KIND`, `OWNER_INTENT`, `INTENT_REVISION`, `CHANGE_RISK`, and exact `EXECUTION_PROFILE` / `EXECUTION_PROFILE_REVISION` binding. Verify that `Next Action` directly advances the packet `Goal` and current owner intent. If they materially disagree, synchronize the packet from the current explicit owner instruction and repository evidence. Ask only when the intended scope itself is genuinely ambiguous; do not search unrelated chats or repositories to invent authority.
9. For legacy packet v2, require its v2 identity/intent fields, require current execution-profile compatibility, and migrate it to v3 on the next meaningful update.
10. Reject packet v1 and versionless packets as non-runnable; migrate them to v3 before implementation.
11. Zero matches: reconcile paused/completed/stale packets and inspect the canonical roadmap for the next authorized dependency-eligible task. Under an explicit owner request, create or refresh its repository-scoped packet from current evidence and continue; do not stop solely because coordination state needs repair.
12. Multiple matches: select by explicit owner priority, dependencies, PRIORITY and branch context. Ask only if competing intents or unsafe ownership conflicts remain after this reconciliation.
13. Verify actual repository branch, HEAD, dirty state, PR/CI state, and relevant canonical files before acting.

Never treat a stale packet HEAD as current truth.

## Resume context budget

After resolving Git identity and before ordinary work:

1. Check whether repository `AGENTS.md` and `.engineering/project.yaml` exist.
2. If they exist, read them first.
3. If the repository shows Engineering System adoption markers (for example `.engineering/`, managed `engineering-system.yml`, or session-continuity adapters) but mandatory `AGENTS.md` or `.engineering/project.yaml` is missing or unreadable, record `ENGINEERING_SYSTEM_ADOPTION=INCOMPLETE` and fail closed unless the selected packet is an explicit adoption-repair flow (`TASK_KIND=ADOPTION`).
4. If they are absent because the repository has not yet adopted the Engineering System or adoption is intentionally pending in a separate workstream/PR, record `ENGINEERING_SYSTEM_ADOPTION=ABSENT_OR_PENDING` and continue under the canonical Engineering System default. Do not create, merge, or modify adoption files unless the current Work Packet explicitly authorizes that work.
5. Read test/release metadata only when relevant and only if present for the adopted project state.
6. Read only canonical references required by `Next Action`.
7. Use minimum sufficient reasoning/context; do not request maximum reasoning by default.
8. Do not preload all references named in the packet.
9. Record CI/review/deploy waits concisely and continue independent authorized work in the same runtime. Use an available watcher for later re-entry when useful; coordinator/automation availability is not an ordinary execution prerequisite. If no runnable work remains, persist the wait and report it honestly.
10. For an existing branch/PR, orient from the base diff first (`git diff --name-only`/`git diff --stat`) before broad repository search.
11. When task-local files are not already obvious and the worktree is clean, use `python3 tools/engineering-context.py --task "<bounded non-secret task phrase>"` before broad repo-wide grep/read. The orientation is bound to exact `HEAD`, ranks only Git-tracked relative paths plus declared canonical knowledge metadata, emits no file content, and is a JIT read hint rather than authority. `ORIENTATION_DECISION=NO_MATCH` or insufficient evidence permits bounded expansion; dirty worktrees fail closed rather than presenting a stale HEAD map. After canonical Work Packet and local repository context are resolved, a configured derived-context adapter may provide bounded project/workstream-scoped JIT enrichment when cross-project, historical, or memory context would materially help. Its output is derived/non-authoritative and must never override current Git/GitHub/repository truth. Adapter unavailability does not block ordinary development; continue on canonical/local context and make the missing enrichment observable. Retired knowledge systems must not be queried as active fallbacks.
12. For a large optional text candidate, prefer `python3 tools/engineering-context.py --task "<bounded non-secret task phrase>" --slice-path <relative-path>` before a full read. Slice mode reads the exact-HEAD tracked UTF-8 blob and emits bounded JSON-encoded task-relevant line windows in source order. It is a context-reduction hint only: `SLICE_DECISION=NO_MATCH`, truncation, or insufficient evidence permits a bounded full read. Never use slicing as a substitute for mandatory `AGENTS.md`, `.engineering/project.yaml`, managed rules, protected Work Packet state, acceptance criteria, or a canonical reference the task requires in full.
13. Bound tool output: retain verbose logs outside model context and surface exit status plus focused grep/tail evidence; expand only on failure or ambiguity. For explicitly eligible line-oriented output that must remain available during the same task, `tools/context_tool_output.py` may emit deterministic bounded head/tail + task/diagnostic line records while storing the exact original only through the existing private `context_fold.py` store. The recovery marker is a retrieval handle, not evidence authority. Protected/authority output must not be reduced; bypass must be store-free; truncation never implies semantic equivalence or provider token/cost savings.
14. Durable authority stays in the Work Packet; stale conversation is not authority. The selected implementation runtime may resume from durable GitHub packet/repository facts without the prior transcript. Verify the actual repository/branch/HEAD before mutation and use stronger approval boundaries only where the action risk requires them.

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
5. Completion of one workstream returns control to roadmap/portfolio priority. Under a repository-level continue request, this return is an internal scheduling transition, not a user-visible stop: select and execute the next dependency-eligible runnable roadmap/release workstream. Stop only when the roadmap/release objective is complete, no dependency-eligible runnable work remains, or genuine owner input/credentials/approval is required. Do not immediately reopen the same theme merely because its follow-up backlog is non-empty.

### Default depth budget

Use a soft default for ordinary bounded work:

```text
implementation -> task/risk-specific audit -> corrective pass when needed -> verification -> complete packet -> schedule next authorized roadmap task
```

One implementation pass, one required terminal audit/review, and at most one ordinary corrective re-audit is the normal depth target. Independence is added only when the task/risk contract requires it. This is a **portfolio/attention budget**, not a safety waiver.

- If the corrective re-audit finds no BLOCKING defect, complete that packet, record remaining findings as follow-up work, and schedule the next authorized roadmap task.
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

The Work Packet/objective is durable. Conversational context is disposable. The selected implementation runtime may roll over to a fresh context and resume from authenticated durable state; it must not depend on transcript continuity.

A coordinator or equivalent outer loop should, when automation exists:

- select only dependency-eligible ACTIVE work;
- apply Work Packet sizing (`tools/work_admission.py size`) and WIP/resource admission (`tools/work_admission.py admit`) before starting a worker;
- keep separate worktrees/state ownership for concurrent workers;
- reconcile actual Git/PR/CI/runtime state after coordinator or worker restart;
- distinguish transient retry from semantic re-plan;
- restart or replace a crashed/stalled worker without inventing new scope;
- preserve terminal evidence and hand human-required decisions to the owner.

Do not encode a brittle micro-step state machine that requires one implementation context to survive the whole workstream. The same outcome may span multiple fresh implementation contexts because GitHub Work Packet and repository state are durable authority.

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
- the planner performs no model call, process spawn, GitHub mutation, merge, owner-notification send, or schedule.

`plan` returns exit 0 when it emits a decision. Exit 3 is reserved for malformed or execution-keyed facts. The decision schema is `schemas/coordinator-decision.schema.json`.

## Bounded coordinator watch

The watch evaluator is a pure function from structured facts plus durable watch state to one re-entry result. It calls the planner. It does not poll, spawn processes, call GitHub, merge, or send notifications. Host scheduling and delivery are a separate later adapter.

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
- the evaluator performs no subprocess, network, GitHub mutation, merge, owner-notification send, or session stop.

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
- `NOTIFY_OWNER` uses that same unconsumed dispatch check, then one bounded `INFO` delivery through the fixed host owner-notification helper; `NOTIFICATION_DELIVERY=VERIFIED` requires a durable adapter receipt. `COMPLETE` is reserved for whole Work Packet completion and is not emitted by a watch pass;
- before send, the host atomically reserves the dispatch for that effect digest and executor attempt. Another effect or attempt cannot send it. Final consumption happens only after the receipt is durable. A crash before send stays retryable by the same attempt. A crash after the send marker without a receipt is reconcile-blocked and is not retried;
- a changed intent revision or subject, a resource `BLOCK`, or an admission `DENY` before delivery yields zero actions and does not stop or mutate unrelated sessions;
- an ambiguous prior action outcome blocks retry; the same applied key or a consumed dispatch is deduplicated;
- missing or invalid trusted dispatch denies the external or worker effect;
- persisted watch state and the action ledger store bounded metadata only;
- lock, watch-state, ledger, and effect-journal paths are derived from the watch identity under the host state root. Caller filenames that differ are rejected. Symlink and path escape fail closed before mutation;
- authoritative packet, branch, subject, and CI facts come from `collect_authoritative` through fixed `/usr/bin/gh` provenance, not caller `PATH`. Git dirty/unpushed and worker liveness/progress are used only when the pinned worktree and session are that authoritative branch at its exact HEAD; a mismatch stays unobserved and is not attributed to the current packet. Resource and admission come from trusted machine observation or stay `UNKNOWN`. Work Packet text cannot set those passing values, and a session does not receive `starting_intent_revision` unless a claim independently binds the same repository, workstream, revision, and pinned worktree. A missing, invalid, or different claim worktree stays unbound. Caller-selected fact files and the request branch cannot supply both sides of an authority comparison;
- the host performs no merge, caller-selected command, session stop, or model call. GitHub delivery is one fixed `gh api` issue comment. Owner delivery is one fixed INFO send through the host-configured notification adapter.

`run-once` returns exit 0 when it emits a result. Exit 3 is reserved for malformed or execution-keyed requests. The result schema is `schemas/coordinator-watch-host.schema.json`.

## Trusted worker external-write adapter

The planner stays decision-only. Ordinary authenticated repository Issue/PR coordination does not use this adapter. Explicitly high-risk external writes and publication effects (production, destructive, credential/permission-boundary, irreversible publication, or a stricter project-defined equivalent) go through the trusted adapter, which authorizes at most one typed effect and does not itself perform the mutation:

```bash
python3 tools/worker_adapter.py evaluate --request-json <facts.json>
```

Immediately before a write, re-read the authoritative Work Packet and compare it with the bound `target_repo`, `workstream`, `intent_revision`, `subject_head`, `branch`, and `requested_action`. The request digest also covers the concrete mutation target and content. A trusted effect is accepted only when `skills-contract` `authorize` verifies host-signed binding and dispatch assertions for that exact digest and `network.post` / `external_write`. Caller-supplied permission, `trusted_dispatch.result=PASS`, or a matching hash without that verification cannot authorize a write. A missing host trust boundary fails closed and does not authorize. `author_association` does not authorize.

A revision, target, or head mismatch returns `STALE_WORKER` and authorizes zero writes, including the case where a worker prepared an Issue body under an older intent revision. An unknown mutation result returns `RECONCILE_AMBIGUOUS` and must not be retried blindly. The same applied digest returns `NO_CHANGE`. Resource `BLOCK` returns `RESOURCE_BLOCKED` and never stops unrelated sessions. The result schema is `schemas/worker-adapter-result.schema.json`.

## Human-attention and notification budget

Human attention is a constrained engineering resource.

**Engineering completion vs owner notification:** engineering truth is determined by the Work Packet completion contract and exact-HEAD deterministic/review/release evidence. Record that separately as `ENGINEERING_COMPLETE=PASS|BLOCK`. For a bounded COMPLETE outcome, attempt one verified delivery through the host-configured owner-notification adapter bound to exact repository/workstream/terminal HEAD with `tools/terminal_completion_notify.py` and record `OWNER_NOTIFICATION=PASS|RETRY_PENDING`. `DELIVERY_VERIFIED=PASS` proves delivery only; failed, unavailable, ambiguous, or stale delivery is a notification-transport obligation and must not rewrite a valid engineering PASS into a code/quality failure. Do not notify for micro-steps or individual test invocations; coalesce one notification around the bounded completed outcome. If the selected implementation runtime must stop because a genuine owner action, credential/permission, infrastructure failure, or irreconcilable external dependency prevents further progress, send one verified BLOCKED notification when possible before returning control. Normal CI waiting, bounded retry/backoff, or work the selected implementation runtime can perform directly is not BLOCKED and must not notify.

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

## Current managed execution profile

Provider/runtime choice is replaceable execution-profile state, not a permanent core invariant. The selected profile is the only runtime-selection authority; conversation history and provider names in prose are never mutation authority. For ordinary repository engineering, the conjunction of current explicit owner intent, a freshly authenticated open ACTIVE `[AI Work]` packet with write/maintain/admin provenance, matching `TARGET_REPO` / `WORKSTREAM` / branch / HEAD / intent revision, execution-profile identity/revision, `packet-lint=PASS`, and current repository/worktree identity is sufficient for the selected runtime to implement, test, audit, commit/push, and perform ordinary authenticated GitHub Issue/PR coordination directly. Do not require another magic phrase, an alternate implementation adapter, a host trust anchor, or `worker_adapter.py` for those normal actions. Re-read mutable packet/profile/branch/HEAD facts immediately before ordinary GitHub writes and reject stale state.

For an effect explicitly classified as high-risk by the core or a stricter project contract—production mutation, destructive action, credential/permission-boundary change, irreversible publication, or equivalent—use the stronger external coordinator / trusted local-binding / signed-dispatch boundary. In that high-risk path, the worker-writable target copy of `python3 tools/implementation_preflight.py check` is never authority; use the immutable canonical helper or an equivalent host-administered copy and require local binding to the authorized worktree. High-risk external Issue/PR mutations represented by the worker-adapter mutation schema require `worker_adapter.py` `APPLIED`. Production shell/deploy effects instead require the fixed trusted production coordinator/signer plus `skills-contract.py authorize=ALLOW` for the exact `shell.production_write` request; on a root-only host this may use only the separately installed root-owned production approver policy and public GitHub packet/repository evidence. If the applicable high-risk trust boundary is unavailable, block that high-risk effect only; do not convert its absence into a blocker for unrelated ordinary development or GitHub coordination.

When the user asks to continue/resume an existing engineering workstream:

- resolve and bind the target repository from the owner's current explicit project/repository request
- **No-active-packet bootstrap fast path:** after one bounded authenticated lookup, if the bound repository has no open `STATUS=ACTIVE` Work Packet, do not repeatedly search packets, reconstruct prior chat history, or remain in orientation/planning. Inspect the repository's durable roadmap/release Issues plus actual Git/PR state once. If a dependency-eligible runnable roadmap outcome exists, create or reactivate exactly one authoritative Work Packet for that outcome, lint/bind it, and make measurable execution progress in the same turn. If no runnable outcome exists, report that concrete terminal/waiting state; absence of an ACTIVE packet is not itself a blocker and must not cause an orientation loop.
- load only that bound repository's active Work Packet; cross-project handoffs/dependencies remain read-only context
- synchronize the packet with the owner's latest explicit request before direct implementation or optional adapter handoff
- lint the exact fresh authoritative packet body with `python3 tools/context_epoch.py packet-lint --expect-target-repo <bound-owner/repo>` and require PASS; `TARGET_REPO_SCOPE_MISMATCH`, stale/mismatched execution-profile identity, or revision is non-runnable
- on workstream selection or a material intent/profile change, validate the fresh packet once; re-read mutable identity before external writes. Routine shell/test commands in the same verified workstream do not require a new preflight ceremony. Never treat a handoff comment as authority
- **Verified next-chat resume:** when the previous handoff transaction already persisted and verified the authoritative packet, a fresh chat must not replay handoff validation, reconstruct the prior transcript, or rewrite an unchanged packet before starting. Re-read the current Issue body and mutable repository/worktree/HEAD/profile facts once, reject only an actual mismatch, then enter the verified Next Action immediately. Re-run packet lint or materially update the packet only when those fresh facts or owner intent changed.
- replace the bound expected repository only after a new explicit owner project/repository switch
- verify `TASK_KIND` / `OWNER_INTENT` / `Next Action` coherence for packet v2/v3, and require exact profile identity/revision binding for v3
- verify current GitHub/repository facts
- continue from `Next Action` only when it still matches the current owner intent
- do not ask the user to paste prior chat unless the required durable state genuinely does not exist

After preflight PASS, the selected implementation runtime may implement directly through the authorized remote path, run affected validation, and perform packet-authorized Git/GitHub writes. Before later external writes or terminal actions, re-read authoritative packet/profile/HEAD facts and reject stale intent. A fresh implementation context resumes from GitHub durable state plus repository facts and must not require the prior conversation transcript.

The selected implementation runtime also owns terminal audit by default. It must re-read current Work Packet, selected execution profile, exact HEAD, PR/CI, tests, and actionable review state instead of treating its own implementation narrative as evidence. HIGH/CRITICAL, security, production, release-authority, permission, credential-boundary, or destructive changes require deeper exact-HEAD machine evidence and any applicable human approval, but do not require a separate model/provider actor. A distinct reviewer, a fresh independent implementation context, or the provider-neutral independent verifier remains optional defense-in-depth/escalation and must not become a provider- or quota-dependent default blocker.

## Execution loop, CI, and parallel work

Keep the normal engineering loop short:

```text
understand -> implement a coherent small/medium batch -> affected local tests -> iterate until locally clean -> PR/fast CI when useful -> address blocking findings -> merge
```

Guidance:
- A next-chat handoff is a transaction, not prose generation. Before returning a continuation token, update the authoritative Work Packet with its accurate ACTIVE/PAUSED/BLOCKED/COMPLETE state and current exact repository/workstream/HEAD/Next Action, re-read that GitHub Issue, and require `python3 tools/handoff_contract.py --facts <facts.json>` PASS. A handoff persists the actual lifecycle state; it must not reactivate waiting or blocked work just to obtain a continuation token. A prompt-only/copy-paste handoff, an unverified Issue write, or a token returned before the authoritative re-read is incomplete and must not be reported as a successful handoff.
- A progress update is not a handoff. Continue while a safe authorized next action exists. If a trusted runnable implementation packet is already selected, make measurable progress in the same turn instead of returning control after a statement of future intent. Reproduction, bounded investigation that resolves a material uncertainty, deterministic validation, repository mutation, or an authorized external state transition qualifies; do not force mutation when diagnosis or validation is the correct next action.
- Prefer the cheapest relevant local tests during implementation. Fast CI is feedback, not release qualification; do not run expensive/full/release suites after every edit.
- Batch related corrective findings before the next expensive qualification run.
- When a workstream is waiting on CI/review/deploy or another machine-observable condition, persist the named wait and yield that workstream back to repository-level scheduling. Select the highest-priority dependency-eligible independent ACTIVE Work Packet/worktree when safe rather than polling or stopping. The single matching ACTIVE packet rule is scoped to the current branch/workstream and must not be interpreted as repository-wide serialization behind a waiting packet.
- Parallel work is allowed when dependencies are satisfied and worktrees, owned paths, shared mutable runtimes, and irreversible external effects do not conflict. Use a separate worktree/state owner for concurrent mutation.
- Use `tools/work_admission.py` when conflict/resource ownership is ambiguous or multiple workers need machine-enforced claims; it is not mandatory ceremony for obviously independent single-runtime work.
- Stop only for a genuine owner decision/credential, an irreconcilable blocker, or an explicit status-only request. Completing a bounded packet/PR/test phase is a scheduling boundary, not a repository-level continue/resume stop. For a repository-level continue request, immediately reschedule to the next dependency-eligible runnable roadmap/release workstream and continue until the roadmap/release objective is complete or no runnable work remains.

## Product execution ownership and supervisor recovery

Normal product engineering is owned by the product repository execution context. A repository-level continue/resume for DRLink, DR Control, DRAtlas, or another adopted product must be executed from that product repository's authoritative Work Packet, roadmap, worktree, and tests. The Engineering System repository is the **control plane**, not the default product execution plane: it owns shared policy, lifecycle/scheduling contracts, adoption tooling, watchdog/re-entry semantics, and recovery of systemic coordination defects.

When a product execution context is healthy, the Engineering System context MUST NOT become a shadow product coordinator, duplicate its Work Packets, or routinely curate its product Issues/PRs. Product-local lifecycle cleanup belongs to the product context: update the existing authoritative packet while its bounded outcome remains coherent, close `COMPLETE` packets, close or supersede stale PRs, and create a new Issue only for a genuinely independent workstream that should survive the current packet. Findings discovered inside an active packet are not by themselves a reason to create more Work Packets.

Supervisor fallback is appropriate when current evidence shows a systemic execution failure such as repeated plan/status-only returns under a continue request, repeated failure to enter a runnable packet, stale/contradictory packet state that the product context does not self-heal, retired-runtime selection, wrong-repository retargeting, uncontrolled Issue/packet proliferation, or repeated identical blocker loops. Supervisor recovery must use the smallest scope that restores product autonomy:

1. diagnose whether the defect is canonical policy/tooling, adopted-policy drift, or product-local coordination state;
2. fix canonical Engineering System policy/tooling first when the defect is systemic and add regression evidence;
3. upgrade/repair the affected adopted repository metadata when required;
4. repair only the minimum stale product coordination state needed to restore a single truthful runnable path;
5. verify the product context can again select/execute its own current packet; then return product implementation and routine Issue/PR lifecycle ownership to that product context.

Supervisor fallback MUST NOT be used merely because the supervisor can access the product repository. It does not authorize the Engineering System context to absorb product roadmap implementation, user-gate execution, or routine product backlog grooming. An actively progressing, owner-authorized product worker is observed rather than replaced; recovery must not mutate its dirty worktree or create a competing implementation lane. Runtime/session failure outside repository control may still require a new product execution context, so repository policy cannot guarantee chat-process liveness; durable Work Packet and Git state are the recovery boundary.

Release qualification follows `standards/RELEASE.md` and the target repository release profile. Product-specific choreography belongs in that repository, not in this continuity standard.

## Optional independent verifier for additional terminal evidence

Normal terminal audit is owned by the selected implementation runtime and can complete in the implementing context when exact-HEAD deterministic evidence and current mutable gates support PASS. The implementer's self-report is never the completion oracle by itself. A coordinator may additionally evaluate structured evidence with the provider-neutral independent verifier when defense-in-depth, escalation, or an explicitly requested independent check is useful.

```bash
python3 tools/independent_verifier.py verify --request-json <evidence.json>
```

Contract:

- bind verification to an exact 40-char subject HEAD; different-HEAD or missing subject identity fails closed;
- independent-verifier use is optional for every risk level; if a verifier actor is supplied, its `identity` and `context_id` must both be distinct from the implementer because the result is being claimed as independent evidence;
- `CHANGE_RISK=LOW|MEDIUM|HIGH|CRITICAL` completion remains aligned with `standards/QUALITY.md`; absence or quota exhaustion of an optional independent-review provider is not itself a completion blocker;
- every declared completion-oracle evidence record must be `PASS` on the same subject HEAD; `FAIL` / `BLOCK` / `NOT_RUN` / unexecuted cannot be promoted;
- actionable review findings must be `FIXED`, `EVIDENCE_DISPOSITION`, or `NOT_ACTIONABLE`;
- when the independent verifier is invoked for HIGH/CRITICAL, it requires mutable CI/review/runtime evidence that carries current `subject_id` + `version_id` (not a historical generic PASS);
- request `expected_mutable` declares the required subject/version contract; each mutable evidence item must match it, and CI `version_id` must equal the subject HEAD (stale/unrelated CI DENYs as `STALE_MUTABLE`);
- CRITICAL additionally requires present human approval when marked required;
- the verifier evaluates JSON facts only and refuses request keys that imply command execution (`command`, `shell`, `argv`, `execute`, …).

`verify` returns `PASS` or `DENY` with an explicit class such as `SAME_ACTOR`, `VERIFIER_REQUIRED`, `HEAD_MISMATCH`, `ORACLE_NOT_PASS`, `REVIEW_OPEN`, `MUTABLE_MISSING`, `STALE_MUTABLE`, `HUMAN_APPROVAL_MISSING`, or `EXECUTION_FORBIDDEN`. Exit `3` is reserved for malformed/untrusted facts.

## Efficiency telemetry and task budget

P0b records verified exact-head outcomes against cost, time, rework, and human intervention. Collection is provider-neutral, local, and off unless a repository writes a record.

- Persist only a generated run ID, repo, workstream, task kind, the session profile, timestamps, exposed usage fields, counts, validation IDs, exact-head evidence, and terminal PASS, BLOCK, or FAIL.
- Do not persist prompts, conversation, source, tool payloads, secrets, logs, or absolute local paths. Additional fields fail closed.
- Missing provider usage, cache, or cost stays null. Do not estimate.
- Default output is `engineering-system/telemetry/` under the repository's absolute Git directory (`git rev-parse --absolute-git-dir`). That location is Git metadata, so generated records stay outside the tracked worktree for canonical repositories, adopted repositories, and linked worktrees. The directory is bounded to 32 records and easy to disable with a `DISABLED` marker. A textual `.gitignore` rule is not the retention boundary. There is no automatic network export.
- Capture provider, model, reasoning, and toolset at session start. A later change requires a recorded justification. Do not switch profiles silently.
- Soft task budgets are optional. Exhaustion yields terminal `BLOCK` with disposition `YIELD`. Further retries fail closed.

## Next-chat handoff transaction

A request to move work to the next chat is a durable-state transaction, not a prompt-writing task.

Required order:

1. Freshly re-read the authoritative Work Packet and actual repository/branch/HEAD state; retain its accurate ACTIVE/PAUSED/BLOCKED/COMPLETE lifecycle state.
2. Replace stale diary/history with the bounded current Current State, Next Action, Latest Evidence, and Blockers needed to resume.
3. Persist that exact Work Packet body to the repository-scoped GitHub Issue.
4. Re-read the persisted Issue body and verify its SHA-256, target repository, accurate canonical lifecycle state, execution-profile binding, and packet lint. An ACTIVE packet with blockers or pending wait metadata is contradictory and must be normalized before handoff.
5. Only after that verification succeeds, return the minimal repository/lane continuation token to the owner.

Use `python3 tools/handoff_contract.py --facts <json>` for the final fail-closed transaction check. A prose prompt, continuation token, or chat summary emitted before the authoritative Issue update is verified is an incomplete handoff and must not be reported as success. The final user-visible handoff should be only the minimal continuation token unless the owner explicitly asks for more detail.

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

A genuinely independent workstream gets a new Issue rather than reusing an unrelated completed packet. Before creating one, search the repository open/paused roadmap and Work Packets for the same durable outcome and update/reuse the existing canonical Issue when present. Never create a new Work Packet merely to record progress, a phase transition, one test finding, one bug-fix iteration, CI waiting, or the next micro-step of an existing outcome. When an outcome reaches COMPLETE, close its Issue in the same lifecycle reconciliation; do not leave completed packets open as scheduler input.

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
- ensure the selected implementation runtime resolves the repository and current durable state first
- never store secrets in Work Packets
- never use a Work Packet update as release evidence
