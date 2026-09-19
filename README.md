# Data Relay Labs Engineering System

Shared engineering standards, AI-assisted development workflows, testing, release governance, and operational practices for Data Relay Labs projects.

## Purpose

This repository is the canonical **Solo AI Engineering System** for Data Relay Labs. It is optimized for a solo developer working with ChatGPT, Cursor, GitHub, and deterministic automation.

It covers the complete engineering lifecycle:

requirements / decisions -> development / bugfix / refactor -> testing / regression / UX / resilience -> security / dependency / OSS supply chain -> release / upgrade / rollback / deprecation -> operations / observability / backup / recovery -> incident / RCA -> knowledge feedback / continuous improvement

## Canonical domains

| Domain | Canonical standard |
|---|---|
| Core lifecycle, roles, Definition of Done | `standards/CORE.md` |
| Development, bugs, refactor, compatibility, migration, dependencies | `standards/DEVELOPMENT.md` |
| Quality, regression, UX, compatibility, performance/resilience | `standards/QUALITY.md` |
| Test levels, triggers, scenario metadata | `standards/TESTING.md` |
| Security, secrets, dependency/OSS/supply chain | `standards/SECURITY.md` |
| Version, artifacts, qualification, upgrade, rollback, EOL | `standards/RELEASE.md` |
| Operations, observability, backup/restore, incidents, DR | `standards/OPERATIONS.md` |
| Documentation, ADR, Product Master, Wiki/SSOT | `standards/KNOWLEDGE.md` |
| ChatGPT/Cursor/GitHub enforcement | `standards/ENFORCEMENT.md` |

## Operating model

Request -> mandatory context load -> classify change -> identify affected domains -> define behavior/invariants -> implement smallest correct change -> run affected tests -> add regression for defects -> run wider/security/lifecycle gates when required -> review/commit -> exact-candidate release qualification when applicable -> operate/observe -> feed incidents and field findings back into regression knowledge

## Repository layout

- `standards/` — canonical lifecycle standards
- `ai/` — shared AI-agent behavior
- `templates/` — files copied/adapted into project repositories
- `schemas/` — machine-readable project/test/release metadata schemas
- `.github/workflows/` — reusable compliance/test/release gates
- `.engineering/` — this repository's own profile

## Enforcement

An adopted repository is expected to use:

AGENTS.md
.cursor/rules/engineering-system.mdc   # alwaysApply: true
.engineering/project.yaml
.engineering/tests.yaml
.engineering/release.yaml

Cursor receives the repository rule persistently. GitHub CI validates adoption and deterministic gates. ChatGPT requires the companion Project/Custom Instruction because Git alone cannot inject instructions into an unrelated chat session.

## v1 principles

1. Keep standards compact and executable.
2. Use project-native tools; do not build a custom CI/test-management platform without evidence that it is needed.
3. Bugs create durable regression knowledge whenever practical.
4. AI proposes, implements, and reviews; deterministic evidence decides PASS/FAIL.
5. Run cheap affected tests during development and expensive operational E2E near release.
6. A release is qualified only for the exact source revision that passed its gates.
7. Security, compatibility, migration, rollback, and operations are part of engineering—not afterthoughts.
8. Real incidents/manual findings feed back into tests, runbooks, ADRs, or requirements.
9. Git is canonical for normative engineering state; Wiki is derived/searchable context.

Start with `standards/CORE.md`.