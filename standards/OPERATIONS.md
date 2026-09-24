# Operations Standard

Production-oriented projects must define the applicable operational lifecycle.

## Lifecycle

As applicable:
- install
- initial configuration
- health/status
- monitoring/observability
- backup
- restore
- upgrade
- rollback
- restart/reboot
- diagnostics/support bundle
- uninstall/reinstall
- incident recovery
- disaster recovery / rebuild
- retirement/export

## Health and observability

Operationally meaningful systems should define:
- authoritative health/status semantics
- actionable logs
- metrics/alerts where useful
- stale/no-data/error distinctions where relevant
- diagnostic evidence that avoids secret leakage

Do not rely on noisy logs as a substitute for defined health.

## Backup / restore

For persistent state:
- identify what is backed up
- validate restore, not just backup creation
- record compatibility/version constraints
- define secret handling
- define partial/corrupt backup behavior
- periodically prove a representative recovery path when risk warrants

## Runbooks

Runbooks focus on executable actions, expected observations, safety boundaries, stop conditions, validation, and recovery. Avoid duplicating architecture documentation.

## Incident lifecycle

detect -> stabilize -> preserve evidence -> understand/root cause -> regression -> fix -> affected qualification -> runbook/ADR/monitoring update

Meaningful incidents should record impact, timeline, root cause, why controls missed it, corrective actions, and verification.

An Incident Packet is a repository-scoped GitHub Issue for current incident control state. It is distinct from an AI Work Packet. `python3 tools/incident-evidence.py check-packet --packet-file <file>` accepts one valid packet and rejects missing, unknown, or contradictory header state. The checker reports `AUTHORITY=NONE`. `SAFETY_FREEZE=ON` reports `SAFETY_EFFECT=NARROW` and only narrows execution. `SAFETY_FREEZE=OFF` or `N/A` reports `SAFETY_EFFECT=NONE`. Neither state grants production, destructive, or mitigation authority. Evidence capture success does not authorize mitigation.

Before mutation, capture the core pre-mutation bundle with `python3 tools/incident-evidence.py capture --root <repo> --incident-id <INC-id>` from the canonical Engineering System checkout. The bundle records Git HEAD, branch or detached state, dirty flag, and changed-file count; Linux memory, swap, load, and PSI when those sources are available; and the aggregate Cursor persistent-session count from the fixed read-only preflight path. It does not record environment variables, secrets, command lines, chat or session identifiers, absolute workspaces, raw remote URLs, file names, or file contents. Unsupported or unavailable sources stay explicit. A missing nonessential source yields `PARTIAL` and does not invent zeros. Failure to establish Git identity or the retention boundary yields `BLOCK` and writes no bundle.

The artifact is written only under `<git-dir>/engineering-system/incidents/<incident-id>/<capture-id>.json`. User-facing output cites the Git-local relative reference. Retention has fixed count and size bounds. Reaching a bound fails closed and does not delete existing evidence. Observable pressure facts may include low available memory, material swap use, elevated memory or I/O PSI, and a high persistent-session count. Those facts are not a root cause. The report includes `ROOT_CAUSE=UNPROVEN`.

This core capture does not run project health, smoke, E2E, logs, container inspection, or deployment commands. Those remain later, separately authorized work. The capture does not stop or mutate Cursor sessions.

## Disaster recovery

For systems whose loss would materially affect users, define the minimum viable rebuild/restore path and dependencies. Test only to the level justified by project risk.

## Resilience

For multi-component systems, test realistic maintenance/failure conditions:
- component restart while others operate
- temporary dependency loss
- degraded state and recovery
- config/policy changes under load
- backup/restore
- state preservation required by contract

Never perform uncontrolled destructive testing on production/customer systems.

## Repository operations contract

Engineering System 1.6 production-oriented project profiles record the smallest project-native operational contract needed by future humans and AI agents:

```yaml
operations:
  production_oriented: true
  runbook_required: true
  incident_response_required: true
  persistent_state: true|false
  runbook_paths:
    - path/to/runbook.md
  health_command: "<project-native health check>"
  backup_command: "<when persistent state applies>"
  restore_test_command: "<when persistent state applies>"
  upgrade_command: "<when available>"
  rollback_command: "<when available>"
```

A production profile without a real runbook path or health command is incomplete. A persistent-state profile without backup and restore-test commands is incomplete.

These commands are references to project-native behavior; the Engineering System must not invent operational commands merely to fill metadata. Destructive commands such as restore, rollback, or rebuild still require the safety/approval rules in the incident lifecycle.

## Runtime and observability contract

A repository may record an optional machine-readable runtime contract at `.engineering/runtime.yaml`. The file is optional. Repositories without it remain valid, and adoption does not create or rewrite one. Schema: `schemas/runtime-contract.schema.json`.

Health, smoke, and operational E2E have one command authority each:

- health/readiness: `operations.health_command` in `.engineering/project.yaml`
- public smoke: `release.public_smoke_command` in `.engineering/release.yaml`
- operational E2E: `release.operational_e2e_command` in `.engineering/release.yaml`

The runtime file stores those field paths and must not store a second copy of the command strings. An empty authority command is unsupported. The contract does not invent a replacement.

Additive capabilities are per-worktree `start`, focused `logs`, `browser` screenshot evidence, `metrics`, `traces`, and `cleanup`. Each entry is explicitly `supported` or `unsupported`. Unsupported entries contain no command. Supported entries require `scope: worktree` and a non-empty bounded command string. Logs, browser, metrics, and traces also require `fuller_command` so complete evidence has a deterministic path. Those strings stay opaque project-owned text. `python3 tools/runtime-contract.py check` validates structure, rejects a second copy of an authority command, and prints bounded findings with deterministic `FULLER` and `RAW` paths. It does not execute commands and it does not apply a shell-syntax or permission policy. Execution permission belongs to a later phase. The contract does not require an observability vendor.

Default checker output is bounded. A truncated report prints `FULLER` and `RAW` commands. A missing runtime file is `RUNTIME_CONTRACT=ABSENT` and still reports authority support from the project and release profiles.

`python3 tools/runtime_evidence.py collect` runs one health, logs, metrics, or traces command after exact HEAD verification and a host-signed `production.read` dispatch bound to the incident, repository, HEAD, and evidence request. The command comes only from `operations.health_command` or a supported logs, metrics, or traces capability. Packet text cannot supply a command. Start, cleanup, smoke, E2E, deploy, rollback, and restart are rejected. Raw output stays in the Git-local incident boundary. The publishable report is capture metadata only, and `MITIGATION_AUTHORITY=NONE`.

## Development-host session pressure

Shared development hosts can become unresponsive when many Cursor persistent sessions accumulate. Before creating a new persistent session, run `tools/cursor-resource-preflight.py`. `PASS` and `WARN` (exit 0) may proceed. `BLOCK` refuses only the new session. Do not stop, kill, or mutate existing sessions to recover capacity. Record the preflight `RESULT` and `REASON` as evidence. Host monitoring may alert on `WARN` or `BLOCK`; Telegram or another notifier is not part of this contract.
