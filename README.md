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
- repository-scoped AI session continuity without chat handoff accumulation

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
| AI session continuity and repository-scoped Work Packets | `standards/SESSION_CONTINUITY.md` |
| Automated repository adoption and qualification | `standards/ADOPTION.md` |
| Core/adapters and deterministic enforcement | `standards/ENFORCEMENT.md` |

## Automated adoption

The intended entrypoint for a new or existing repository is deliberately simple:

> Apply https://github.com/datarelay-labs/engineering-system to this project.

An AI agent should then follow `standards/ADOPTION.md`: inventory the target repository, classify existing rules, discover project-native tests/CI, preserve stricter project invariants, and use the deterministic bootstrap for the mechanical installation.

Read-only audit:

```bash
python tools/adopt.py --root /path/to/project --audit
```

Managed bootstrap after the repository-specific rule/test review:

```bash
python tools/adopt.py \
  --root /path/to/project \
  --apply \
  --ack-rule-review \
  --ci-mode shared \
  --test-command "<project-native test command>"
```

Managed adoption pins the canonical version and immutable baseline SHA, installs the repository entrypoints, session continuity, pull-request compliance/affected-test wiring, discovers test plus build/lint/typecheck commands when safe, derives conservative domain candidates, resolves production/operations posture, records native CI ownership, reconciles observable GitHub merge enforcement, and validates the result. Engineering System 1.6 production projects also record project-native runbook/health/recovery contracts, while release workflows use an executable `release.yaml` contract with separate qualification and post-release smoke phases.

The bootstrap does not overwrite existing project files. Ambiguous tests, dirty worktrees, unreviewed AI rules, unresolved production/deployment posture, existing CI without an explicit shared/native mapping decision, ambiguous native-CI ownership, and destructive upgrades fail closed.

Existing managed 1.5 adoptions can be upgraded through the fail-closed `tools/upgrade-adoption.py` workflow rather than by blindly rerunning bootstrap.

See `standards/ADOPTION.md`.

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
