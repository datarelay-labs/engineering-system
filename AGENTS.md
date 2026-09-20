# Engineering System Repository Rules

This repository defines the canonical Solo AI Engineering System.

## Mandatory entry sequence

Always load only the minimum high-signal context needed for the task:

1. Read this `AGENTS.md`.
2. Read `.engineering/project.yaml`.
3. For implementation/debugging/testing, read `.engineering/tests.yaml`.
4. For release/version/artifact work, read `.engineering/release.yaml`.
5. Read `standards/CORE.md` plus only the standard(s) relevant to the change.
6. Inspect the exact affected implementation/tests before editing.

Do not load all standards, Wiki pages, archived changes, or historical discussions by default.

If mandatory context is missing or contradictory, fail closed: report the configuration problem instead of guessing.

## Repository-specific rules

1. Keep the system lightweight for a solo developer using AI-assisted development.
2. Prefer existing GitHub/project-native capabilities over custom platforms.
3. Do not add enterprise process unless it produces clear solo-developer value.
4. Prefer the cheapest deterministic test that can disprove correctness first.
5. PR validation should be affected/fast by default; expensive full qualification belongs near release.
6. Do not duplicate an equivalent native project gate merely because a shared gate exists.
7. Stop downstream expensive qualification after a blocking deterministic failure.
8. Changes to schemas/templates/workflows must remain backward-aware.
9. Validate YAML/JSON syntax and reusable workflow structure before merge.
10. Never weaken enforcement/validation merely to obtain PASS.

For changes to repository adoption behavior, read `standards/ADOPTION.md`, preserve backward compatibility for existing adopted repositories, and test the bootstrap/compliance path.

Cursor additionally receives the always-applied `.cursor/rules/engineering-system.mdc` adapter.
