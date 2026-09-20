# Engineering System Adoption Standard

## Goal

A user or AI agent should be able to point at a software repository and say:

> Apply https://github.com/datarelay-labs/engineering-system to this project.

The result should be a bounded, reviewable adoption that preserves project-specific rules, discovers existing tests and CI, installs the common Engineering System entrypoints, wires deterministic PR validation, and proves the adoption before it is considered complete.

Adoption is not permission to redesign the product, replace native CI, weaken tests, or rewrite repository-specific invariants.

## Adoption trigger

When a user asks to apply, adopt, bootstrap, migrate to, or align a repository with the Engineering System:

1. Resolve the exact target repository and current branch/worktree.
2. Read this standard and only the other standards needed for discovered conflicts.
3. Inventory the repository before writing files.
4. Prefer the deterministic adoption tool for mechanical installation.
5. Keep semantic decisions visible and fail closed when they cannot be inferred safely.

## Phase 1 — inventory

Record:

- repository root, origin, branch, HEAD, and dirty state
- languages/frameworks/package managers
- existing build/test/lint/typecheck commands
- existing CI workflows and release workflows
- source/test directory layout
- existing AI instruction surfaces such as AGENTS.md, CLAUDE.md, .cursorrules, .cursor/rules/**, .cursor/commands/**, and repository-specific agent files
- release/version/artifact sources
- product-specific architecture, security, persistence, migration, API, operational, and compatibility invariants

Do not infer that an existing rule is obsolete merely because a canonical rule exists.

## Phase 2 — classify existing rules

Every pre-existing AI/engineering rule touched by adoption must be classified as one of:

- KEEP — valid project-specific rule
- KEEP_STRICTER — valid rule stricter than the common standard
- DUPLICATE — same invariant already supplied by the Engineering System
- CONFLICT — contradicts current product truth or the canonical Engineering System
- OBSOLETE — references retired behavior/tooling
- UNKNOWN — cannot be classified safely yet

KEEP and KEEP_STRICTER rules survive adoption.

DUPLICATE rules may be removed only after the canonical replacement is installed.

CONFLICT, OBSOLETE, and UNKNOWN require explicit evidence or owner review before destructive cleanup.

## Phase 3 — deterministic bootstrap

Use:

```bash
python tools/adopt.py --root /path/to/project --audit
```

The audit is read-only.

After repository-specific commands and rule classifications are known, apply with explicit inputs when discovery is ambiguous:

```bash
python tools/adopt.py \
  --root /path/to/project \
  --apply \
  --ack-rule-review \
  --test-command "<project-native affected/full test command>"
```

The tool installs only missing generated surfaces by default. It does not overwrite existing project files.

For repositories with a known release qualification command, also provide `--release-command`. Provide `--preflight-command` only when the command is a genuinely cheap deterministic release blocker.

## Required adopted surfaces

A managed adopted repository contains:

```text
AGENTS.md
.engineering/project.yaml
.engineering/tests.yaml
.engineering/release.yaml
.cursor/rules/engineering-system.mdc
.cursor/commands/resume.md
.github/ISSUE_TEMPLATE/ai-work-packet.md
.github/workflows/engineering-system.yml
```

The project profile records the Engineering System version and immutable canonical baseline SHA.

Release automation is added only when the project has an explicit project-native release qualification command.

## Test discovery rules

The adoption tool may auto-select a test command only when discovery is unambiguous.

Supported deterministic candidates include common Python, Node, Go, Rust, Maven, Gradle, and explicit repository test runners.

If multiple plausible commands exist or no safe command is found, adoption must stop for an explicit test command rather than inventing one.

Generated test metadata is conservative:

- changed source/test paths map to a project domain
- project-native tests run on affected changes
- cheap static PR guardrails remain separate
- ordinary PR adoption never turns a multi-hour release suite into a default PR gate

## CI wiring

Managed adoption wires:

- adoption compliance on pull requests
- affected-test selection on pull requests
- immutable references to the canonical Engineering System baseline

Release preflight and release gate workflows are wired only when project-specific commands are explicitly known.

Do not duplicate a native gate that already proves the same invariant. In that case, keep the native gate and document the mapping instead of running both.

## Phase 4 — qualification

Adoption is not PASS because files exist.

At minimum verify:

1. required files are present
2. project/test/release metadata parses
3. Engineering System version/baseline identity is valid
4. session-continuity files are present
5. caller workflows reference the same immutable baseline
6. existing rule review was completed
7. affected-test command is real or the repository is explicitly classified as having no executable tests
8. generated workflow YAML parses
9. the cheapest project-native smoke/affected test is executed when practical

Run:

```bash
python tools/check-adoption.py --root /path/to/project
```

Then execute the repository's affected test/CI smoke.

Only after structural validation and the required smoke evidence succeed may the result be reported as:

```text
ENGINEERING_SYSTEM_ADOPTION=PASS
```

## Existing adopted repositories

Do not silently replace an older adoption.

For an upgrade:

1. audit current adoption and local overrides
2. compare the pinned version/baseline with the desired canonical version
3. preserve KEEP / KEEP_STRICTER project rules
4. migrate generated surfaces deliberately
5. rerun adoption qualification
6. keep the upgrade in a separate branch/PR from unrelated product work

## Fail-closed cases

Stop and report the smallest missing decision when:

- target repository is ambiguous
- the worktree is dirty and adoption would risk unrelated work
- existing rules have not been classified
- test command discovery is ambiguous
- a required release command is unknown
- existing generated surfaces would need destructive overwrite
- canonical baseline SHA cannot be resolved
- adoption would duplicate an equivalent native gate without a mapping decision

## Definition of adopted

A repository is adopted when:

- the canonical Engineering System is pinned by version and immutable baseline
- repository entrypoints and session continuity are installed
- project-specific invariants are preserved
- affected tests and compliance checks are wired
- release gates are wired when applicable
- deterministic adoption validation passes
- no required gate is claimed from unexecuted or different-HEAD evidence

The target is automatic preparation with explicit fail-closed boundaries, not blind automation.
