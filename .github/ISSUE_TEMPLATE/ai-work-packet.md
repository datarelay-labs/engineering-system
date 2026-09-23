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

## Goal

State the stable workstream outcome in one concise paragraph.

## Current State

- Keep only facts needed to resume now.
- Replace this section as work progresses; do not append history.
- Tool readiness or waiting state belongs here, not in STATUS.

## Next Action

State exactly the next bounded action or phase.
It must directly advance both Goal and OWNER_INTENT and be compatible with TASK_KIND.


## Handoff Sizing

```text
WORK_PACKET_SIZING=KEEP
INCLUDED_ISSUES=NONE
SIZING_REASON=One medium-sized coherent outcome with a single deterministic completion oracle.
```

Before Cursor/coding-agent handoff, set `WORK_PACKET_SIZING` to `BATCH`, `KEEP`, or `SPLIT`.
Do not assume one GitHub Issue equals one Cursor job. Batch adjacent small Issues/findings that share implementation context and validation; split unrelated outcomes or scope that cannot reliably complete implementation plus validation in one bounded session.

## Constraints

- List only current scope/safety constraints that materially affect execution.

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
