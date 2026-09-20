# Repository Engineering Rules

This repository follows the canonical Engineering System:
https://github.com/datarelay-labs/engineering-system

## Minimum context first

Always:
1. Read this `AGENTS.md`.
2. Read `.engineering/project.yaml`.

Then only when relevant:
3. For implementation/debugging/testing, read `.engineering/tests.yaml`.
4. For release/version/artifact work, read `.engineering/release.yaml`.
5. Read only the Engineering System standard/specification/ADR/runbook needed for the task.

Do not preload all standards, Wiki pages, archived changes, or historical discussions.

When explicitly resuming an existing workstream, resolve this repository first and load its single matching active AI Work Packet. Do not search other repositories or replay old chat history. Verify the actual branch/HEAD/state before acting.

## Execution rules

1. Classify the change and identify affected domains/contracts/security/operations.
2. For material design-bearing changes, apply the canonical `standards/DESIGN.md` minimal design gate before implementation.
3. Inspect relevant implementation and tests.
4. Make the smallest correct change.
5. Run the cheapest affected deterministic tests first.
6. PR validation should stay fast; do not run a full release suite merely because code changed.
7. Do not duplicate an equivalent native project CI gate.
8. A known blocking deterministic failure stops expensive downstream qualification.
9. Bug fixes require durable regression coverage whenever practical.
10. Never weaken a valid test merely to obtain PASS.
11. Never claim release readiness without exact executable evidence.
12. Never reuse qualification evidence from a different source HEAD.

If the user reports an outage, degraded service, failed upgrade, data-loss risk, or other production-impacting symptom, switch to the canonical `standards/OPERATIONS.md` incident lifecycle. Preserve evidence before mutation and do not perform destructive/irreversible recovery without explicit approval unless an approved runbook authorizes it.

If mandatory engineering context is missing or contradictory, stop implementation and report the configuration defect instead of guessing.

When the user explicitly asks to apply/adopt/bootstrap the Engineering System to this repository, use the canonical `standards/ADOPTION.md` workflow: inventory first, classify existing rules, discover project-native tests/CI, preserve stricter project invariants, use deterministic bootstrap for missing common surfaces, and qualify the adoption before reporting PASS. If this repository is already pinned to an older managed Engineering System version, use the fail-closed managed upgrade workflow instead of rerunning initial bootstrap.

Tool-specific adapters must not weaken these rules.
