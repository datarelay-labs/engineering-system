# Engineering System Repository Rules

This repository defines the canonical Data Relay Labs Solo AI Engineering System.

## Mandatory entry sequence

Before planning or changing this repository, every coding/review agent must:

1. Read this `AGENTS.md`.
2. Read `.engineering/project.yaml`.
3. Read `.engineering/tests.yaml`.
4. Read `.engineering/release.yaml` for release-related work.
5. Read `standards/CORE.md` and the standard relevant to the change.
6. Identify affected domains before implementation.

If mandatory context is missing or contradictory, fail closed: report the configuration problem instead of guessing.

## Repository-specific rules

1. Keep the system lightweight for a solo developer using ChatGPT/Cursor.
2. Prefer existing GitHub/project-native capabilities over custom platforms.
3. Do not add enterprise process unless it produces clear solo-developer value.
4. Changes to schemas/templates/workflows must remain backward-aware.
5. Validate YAML/JSON syntax and reusable workflow structure before merge.
6. Document durable methodology changes in the relevant standard.
7. Never weaken enforcement/validation merely to obtain PASS.

Cursor additionally receives the always-applied `.cursor/rules/engineering-system.mdc` rule.
