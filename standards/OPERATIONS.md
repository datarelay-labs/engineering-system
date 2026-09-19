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