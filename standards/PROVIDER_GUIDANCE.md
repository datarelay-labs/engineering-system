# Provider Guidance Alignment Standard

## Purpose

Provider guidance is an upstream design input for the Engineering System, not execution authority. The system periodically reviews official guidance from the AI providers it actually uses and converts only durable, evidenced lessons into canonical policy.

Current reference set, reviewed 2026-10-07:
- OpenAI, *Harness engineering* — repository knowledge as system of record, progressive disclosure, mechanical enforcement of architecture/invariants, autonomy inside boundaries.
- OpenAI Developers, *Rethinking skills and prompts for GPT-6 Astra* — revisit accumulated prompts/skills/AGENTS scaffolding as models improve; avoid bloated context and stale hand-holding.
- Anthropic, *Harness design for long-running application development* — start from the simplest effective harness and remove components methodically while measuring outcome impact.
- Anthropic, *Scaling Managed Agents: Decoupling the brain from the hands* — harnesses encode assumptions that can become stale as model capability improves; keep stable interfaces while allowing harness internals to change.

Official source URLs:
- https://openai.com/index/harness-engineering/
- https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra
- https://www.anthropic.com/engineering/harness-design-long-running-apps
- https://www.anthropic.com/engineering/managed-agents

## Canonical interpretation

Use provider guidance to challenge assumptions, not to copy product-specific advice blindly.

Durable cross-provider invariants:
- repository-local/versioned durable state outranks chat/transcript history;
- owner intent and explicit authorization boundaries remain authoritative;
- correctness is established by observable evidence, not model self-report;
- security, destructive operations, credentials, publication, and release integrity keep explicit boundaries;
- architecture and safety invariants should be mechanically checkable where practical;
- context should be progressively disclosed and task-relevant;
- feedback loops and evals should justify harness complexity;
- provider-specific syntax, model quirks, notification transports, and convenience tooling are replaceable.

## Rule lifecycle

Every new or existing harness rule should be classified as one of:

- `KEEP` — durable invariant with demonstrated value.
- `RELAX` — valid intent but current enforcement is more restrictive than needed.
- `MAKE_CONDITIONAL` — useful only for a risk class, task type, failure mode, or evidence trigger.
- `PROVIDER_PROFILE` — provider/runtime-specific behavior that must not define the core.
- `DELETE` — stale, duplicate, unmeasured, or counterproductive scaffolding.

A rule that exists mainly because a model previously failed at a behavior is scaffolding. Record the failure mode it addresses and a review/removal condition. Do not promote wording, prompt style, or provider identity into a core invariant unless the wording itself is a security/compatibility contract.

## Model/provider change review

After a material provider/model/harness change:

1. keep security/release/root-of-trust boundaries stable;
2. run representative existing evals or regression tasks;
3. remove or relax one suspected stale scaffold at a time;
4. compare verified correctness, rework, latency/cost, and human intervention;
5. retain complexity only when measured outcomes justify it;
6. restore/rollback when removal causes a material regression.

Do not use provider release notes alone as proof that a guard is obsolete.
## Current execution profile

The current runtime defaults are defined by `.engineering/execution-profile.yaml` under explicit owner intent; provider/runtime names are deliberately not repeated here. New Work Packets bind `EXECUTION_PROFILE` plus `EXECUTION_PROFILE_REVISION`. Legacy packet fields are accepted only through compatibility declared by that profile.

Clear current owner intent authorizes ordinary work by the selected runtime after repository/scope verification; a Work Packet is not a mandatory permission object. Authenticate/validate packets when deriving instructions or relying on them for handoff, conflicting scheduling or recovery. Runtime defaults remain profile data; historical adapter text or provider availability never activates another runtime. Persistent default changes require a reviewed execution-profile migration.

The selected implementation runtime performs terminal review directly. A distinct reviewer is introduced only when a specific risk/task contract explicitly requires independent perspective; reviewer-provider availability or quota is never a universal completion dependency.

## Measurable progress, not forced mutation

A runnable packet should make measurable progress rather than stop after a plan/status report. Measurable progress can be:
- defect reproduction;
- bounded investigation that resolves a material uncertainty;
- deterministic test/validation;
- code/config/document mutation when mutation is actually warranted;
- an authorized external state transition.

Do not force repository mutation solely to satisfy an execution ritual.

## Continuation and terminal responses

Completion of one coherent implementation or validation batch is a checkpoint, not by itself a reason to return a terminal response. When the owner's requested roadmap/release objective is still incomplete and safe runnable work remains, the selected runtime should choose and execute the next runnable action in the same turn.

A terminal response is appropriate only when the requested objective is complete, no safe runnable work remains, genuine owner input is required, or an irreconcilable blocker has been observed. Durable Work Packet state exists so work can recover across a real platform turn/session boundary; it is not a substitute for continuing runnable work now. Persistence is recovery, not a reason to yield.

Static policy/eval checks can verify that this contract is present and internally consistent, but they are not proof of live-model obedience. Representative fresh-chat execution remains the evidence for runtime behavior.

## Verification depth

Use `CHANGE_RISK`, task-specific contracts, and observed uncertainty to choose evidence depth. Deterministic tests and exact subject identity are the base. Add wider qualification, independent review, runtime evidence, rollback proof, or human approval only when the risk/contract calls for them.

## Governance-root migration

Base-owned governance execution remains the trust boundary. Candidate PR code must never replace the helper/workflow that evaluates that same candidate.

Canonical `policy_epoch` is policy-freshness state. Repository-local root-of-trust migrations use `governance_epoch`, which advances exactly once for each root migration while policy freshness remains pinned to the canonical value. The base-owned floor reads migration manifests as data and accepts historical contract-v1 policy-epoch migrations for backward compatibility; new contract-v2 migrations bind the governance generation. Same-generation root drift fails closed, and exact-HEAD validation/review plus current owner approval remain mandatory.

## Engineering truth vs notification

Engineering completion is determined by the completion contract and evidence. Notification delivery is operational state. A failed owner-notification transport may require retry and should be visible to the owner, but it does not retroactively make correct code or passing release evidence false.

## 2026-10-07 cleanup decisions and evidence limits

- KEEP: owner scope, target/worktree isolation, high-risk approval and existing base-owned verification; these are not scaffolding to relax for a green check.
- MAKE_CONDITIONAL: packet lint, admission, coordinator/watch and independent-review helpers. Use the concrete ambiguity, continuity, concurrency or risk trigger; ordinary commands do not run the whole toolchain.
- RELAX: root instructions become a task-specific context map; user-gate details stay in the actual contracts. Contract-first, direct-persona, same-candidate and final complete confirmation remain mandatory when those gates are requested.
- DELETE: portable human-hour/file-count sizing heuristics and runtime-specific reset recipes. Use coupling, completion oracles and observed context capacity.
- EVIDENCE: document/routing lint is not live-agent behavior. Deterministic wait checks exercise the optional planner's state transitions, not a model's obedience. Source byte counts are not provider token/cost savings. No live A/B autonomy improvement is claimed without observed comparable runs.
- MIGRATION: a legacy contract-v1 base accepts exactly one policy-epoch increment. Land and verify that bridge first; only its v2 successor may converge policy freshness in a separate change. Already-merged invalid historical transitions cannot be retroactively labelled PASS by a newer checker; retain the discrepancy and verify forward changes honestly.

The official references above inform these decisions; they do not mandate this repository's packet format, helper stack or two user-gate names. Review/remove remaining scaffolding only with evidence, not an arbitrary deletion percentage.

## Access-diagnosis scaffold review

The access-discovery/reporting guidance addresses observed unsupported tool/permission-denial claims and sandbox-only substitution. It adds no permission cache, preflight service, approval bypass, or claim of permanent connectivity. Reassess the added wording after representative fresh-chat tasks: keep it only while it reduces unsupported blockage without extra routine probes or weakened boundaries. Static policy and adoption tests establish propagation, not model obedience.
