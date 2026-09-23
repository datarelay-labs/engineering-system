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

## Development-host session pressure

Shared development hosts can become unresponsive when many Cursor persistent sessions accumulate. Before creating a new persistent session, run `tools/cursor-resource-preflight.py`. `PASS` and `WARN` (exit 0) may proceed. `BLOCK` refuses only the new session. Do not stop, kill, or mutate existing sessions to recover capacity. Record the preflight `RESULT` and `REASON` as evidence. Host monitoring may alert on `WARN` or `BLOCK`; Telegram or another notifier is not part of this contract.
