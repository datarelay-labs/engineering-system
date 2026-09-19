# Data Relay Labs Engineering System

Shared engineering standards, AI-assisted development workflows, testing, release governance, and operational practices for Data Relay Labs projects.

## Purpose

This repository is the canonical **Solo AI Engineering System** for Data Relay Labs. It is optimized for a solo developer working with ChatGPT, Cursor, GitHub, and deterministic automation.

The system standardizes the parts that benefit from consistency:

- development and change lifecycle
- AI-agent operating rules
- test levels and regression policy
- change-impact / affected-test execution
- release qualification and exact-HEAD provenance
- operational lifecycle and incident-to-regression feedback

It intentionally avoids heavyweight enterprise process such as CABs, mandatory multi-human approvals, GitFlow, separate QA/SRE organizations, or full E2E on every change.

## Operating model

```text
Request
  -> classify change
  -> read project context
  -> identify affected domains
  -> define behavior / invariants
  -> implement smallest correct change
  -> run affected tests
  -> add regression for defects
  -> run required wider gates
  -> review / commit
  -> release qualification when applicable
  -> operate
  -> convert incidents into regression knowledge
```

## Repository layout

- `standards/` — canonical engineering standards
- `ai/` — shared AI-agent behavior
- `templates/` — files copied/adapted into project repositories
- `schemas/` — machine-readable project/test/release metadata schemas
- `.github/workflows/` — reusable GitHub Actions gates
- `.engineering/` — this repository's own project/test/release profile

## v1 principles

1. Keep standards short and executable.
2. Use existing project-native tools; do not build a custom CI/test-management platform without evidence that it is needed.
3. Bug fixes require a reproducible regression whenever practical.
4. AI proposes, implements, and reviews; deterministic evidence decides PASS/FAIL.
5. Run cheap affected tests during development and expensive operational E2E near release.
6. A release is qualified only for the exact source revision that passed its gates.
7. Real operational incidents and manual findings feed back into tests, runbooks, or ADRs.
8. Project-specific behavior stays in the project repository; this repository defines the shared method.

## Adoption model

A project adopts the system with:

```text
AGENTS.md
.engineering/
  project.yaml
  tests.yaml
  release.yaml
```

Project repositories keep their existing native test runners and deployment tools. The shared system coordinates when and why they run; it does not replace them.

Start with `standards/CORE.md`.
