# Repository Engineering Rules

This repository follows the Data Relay Labs Engineering System:
https://github.com/datarelay-labs/engineering-system

## Mandatory entry sequence

Before planning, editing, refactoring, fixing, testing, or releasing code:

1. Read this `AGENTS.md`.
2. Read `.engineering/project.yaml`.
3. Read `.engineering/tests.yaml`.
4. Read `.engineering/release.yaml` for release-related work.
5. Follow the Engineering System version/baseline pinned by the project profile.
6. Identify affected domains and public contracts.
7. Inspect relevant existing implementation and tests.
8. Make the smallest correct change.
9. Run affected tests first and wider gates as required.
10. Bug fixes require durable regression coverage whenever practical.
11. Never weaken a valid test merely to obtain PASS.
12. Do not claim release readiness without exact executable evidence.
13. Do not reuse qualification evidence from a different source HEAD.

If mandatory engineering context is missing or contradictory, stop implementation and report the configuration defect instead of guessing.

Cursor must also have `.cursor/rules/engineering-system.mdc` with `alwaysApply: true`.
