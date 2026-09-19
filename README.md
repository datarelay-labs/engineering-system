# Engineering System

Shared engineering standards, AI-assisted development workflows, testing, release governance, and operational practices for software and product projects developed with ChatGPT and Cursor.

## Purpose

This repository is the canonical **Solo AI Engineering System** used as the default engineering standard across the owner's current and future software/product projects, regardless of GitHub organization, repository owner, product name, or project location.

This explicitly includes repositories under organizations such as `datarelay-labs`, `xdr-labs`, and any future related repositories. The canonical repository remains `datarelay-labs/engineering-system`.

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

## Enforcement

The global/project AI instruction bootstraps repository context loading. Adopted repositories then use:

```text
AGENTS.md
.cursor/rules/engineering-system.mdc   # alwaysApply: true
.engineering/project.yaml
.engineering/tests.yaml
.engineering/release.yaml
```

Cursor receives the repository rule persistently. GitHub CI validates adoption and deterministic gates. If a repository is not yet adopted, that is an adoption gap; agents must still follow this canonical Engineering System as the default standard instead of ignoring it.

Start with `standards/CORE.md`.
