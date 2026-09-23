---
name: AI Work Packet
about: Repository-scoped current state for ChatGPT/Cursor session continuity
title: "[AI Work] "
labels: []
assignees: []
---

PACKET_VERSION=2
TARGET_REPO=owner/repository
WORKSTREAM=replace-with-stable-slug
STATUS=ACTIVE
BRANCH=replace-with-branch-or-N/A
TASK_KIND=DEVELOPMENT
OWNER_INTENT=State the owner's current explicit request in one concise line.
LAST_VERIFIED_HEAD=UNKNOWN
PRIORITY=NORMAL
INTENT_REVISION=1
CHANGE_RISK=MEDIUM

## Goal

State the stable workstream outcome in one concise paragraph.

## Current State

- Keep only facts needed to resume now.
- Replace this section as work progresses; do not append history.
- Tool readiness or waiting state belongs here, not in STATUS.
- If a worker is active, record its starting INTENT_REVISION and any named WAITING/STALL condition here.

## Next Action

State the next bounded **outcome / execution bundle**, not one command, one tiny Issue, or one micro-step.
It must directly advance both Goal and OWNER_INTENT, be compatible with TASK_KIND, and be sized to finish implementation plus deterministic validation.


## Handoff Sizing

```text
WORK_PACKET_SIZING=KEEP
INCLUDED_ISSUES=NONE
SIZING_REASON=One medium-sized coherent outcome with a single deterministic completion oracle.
```

Before Cursor/coding-agent handoff, set `WORK_PACKET_SIZING` to `BATCH`, `KEEP`, or `SPLIT`.
Do not assume one GitHub Issue equals one Cursor job. Batch adjacent small Issues/findings that share implementation context and validation; split unrelated outcomes or scope that cannot reliably complete implementation plus validation in one bounded session.

## Completion Contract

- Expected observable behavior/state:
- Deterministic test/oracle or required manual evidence:
- Prohibited regressions/invariants:
- Terminal evidence required (exact HEAD/runtime/CI as applicable):
- Blocking finding classes for this packet:
- Stop condition / sufficiency rule:
- Depth budget: NORMAL (implementation -> independent audit -> corrective pass if needed -> verification -> stop)

## Follow-up Discoveries

NONE

Record meaningful out-of-scope findings as linked Issues/Work Packets. Do not silently absorb them into this packet unless the coordinator explicitly re-sizes the active outcome. Once the completion contract passes and no blocking finding remains, close/complete this packet rather than continuing speculative hardening; non-blocking improvements stay as follow-up work.

## Constraints

- List only current scope/safety constraints that materially affect execution.
- For mutating external actions, note idempotency/reconciliation requirements when retries could duplicate side effects.

## Canonical References

- `AGENTS.md`
- `.engineering/project.yaml`
- Add only task-relevant product/spec/test/release references.

## Latest Evidence

```text
HEAD=UNKNOWN
TARGETED_TESTS=NOT_RUN
CI=NOT_RUN
```

## Blockers

NONE
