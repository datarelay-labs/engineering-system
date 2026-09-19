# Repository Engineering Rules

This repository follows the Data Relay Labs Engineering System:
https://github.com/datarelay-labs/engineering-system

Before modifying this repository:
1. Read `.engineering/project.yaml`.
2. Read `.engineering/tests.yaml`.
3. Read `.engineering/release.yaml` for release work.
4. Identify affected domains before implementation.
5. Inspect relevant existing tests before changing behavior.
6. Make the smallest correct change.
7. Run affected tests first.
8. Bug fixes require a durable regression test whenever practical.
9. Never weaken a valid test merely to obtain PASS.
10. Do not claim release readiness without exact executable evidence.
