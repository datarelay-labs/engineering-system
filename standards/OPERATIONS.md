# Operations Standard

Production-oriented projects should define the applicable operational lifecycle.

## Minimum topics
Install, initial configuration, health/status, backup, restore, upgrade, rollback, restart/reboot, diagnostics/support, uninstall, reinstall, monitoring, and incident recovery as applicable.

## Runbooks
Runbooks focus on executable actions, expected observations, safety boundaries, validation, and recovery. Avoid duplicating architecture documentation.

## Incident feedback loop
```text
incident -> stabilize -> reproduce/understand -> root cause
-> regression test -> fix -> affected qualification -> runbook/ADR update if needed
```

## Resilience
For multi-component systems, test realistic maintenance and failure conditions: one component restarting while others operate, temporary dependency loss, degraded state and recovery, configuration changes under load, backup/restore, and contractually required state preservation.

Never perform uncontrolled destructive testing on production/customer systems.
