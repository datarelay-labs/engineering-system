# Engineering System

A lightweight, cost-efficient Solo AI Engineering System for software/product projects developed with AI assistance.

## Purpose

This repository is the canonical engineering standard across the owner's current and future projects, regardless of GitHub organization, repository owner, product name, or project location.

The system optimizes for:
- small, high-signal AI context
- affected-test-first development
- fast deterministic PR feedback
- no duplicate CI evidence
- release-candidate-only expensive qualification
- exact-HEAD release evidence
- durable regression/incident knowledge

## Default execution model

```text
change
 -> affected tests
 -> cheap PR guardrails
 -> merge

release candidate
 -> fast release preflight
 -> full deterministic qualification
 -> lifecycle/platform
 -> performance/resilience
 -> operational E2E
 -> exact-HEAD release
```

Do not run multi-hour full suites on every PR. Do not start expensive downstream qualification while a known blocking deterministic failure exists.

## Canonical domains

| Domain | Canonical standard |
|---|---|
| Core lifecycle, roles, Definition of Done | `standards/CORE.md` |
| Development, bugs, refactor, compatibility, migration, dependencies | `standards/DEVELOPMENT.md` |
| Quality, regression, UX, compatibility, performance/resilience | `standards/QUALITY.md` |
| Test levels, affected selection, trigger semantics | `standards/TESTING.md` |
| Security, secrets, dependency/OSS/supply chain | `standards/SECURITY.md` |
| Version, artifacts, qualification, upgrade, rollback | `standards/RELEASE.md` |
| Operations, observability, backup/restore, incidents, DR | `standards/OPERATIONS.md` |
| Product Master/OpenSpec/ADR/Wiki source-of-truth roles | `standards/KNOWLEDGE.md` |
| Core/adapters and deterministic enforcement | `standards/ENFORCEMENT.md` |

## Core and adapters

The core standard is tool-agnostic. Tool-specific instructions are adapters. See `adapters/README.md`.

Adopted repositories use:

```text
AGENTS.md
.cursor/rules/engineering-system.mdc
.engineering/project.yaml
.engineering/tests.yaml
.engineering/release.yaml
```

## Context rule

Always load `AGENTS.md` and `.engineering/project.yaml`. Load test/release metadata and only the relevant standard/specification when needed. Do not preload the entire Engineering System or Wiki.

## Canonical source

GitHub is normative. Wiki/Athena is derived/searchable knowledge.

Start with `standards/CORE.md`.
