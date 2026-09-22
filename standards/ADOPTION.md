# Engineering System Adoption Standard

## Goal

A user or AI agent should be able to point at a software repository and say:

> Apply https://github.com/datarelay-labs/engineering-system to this project.

The result should be a bounded, reviewable adoption that preserves project-specific rules, discovers existing tests and CI, installs the common Engineering System entrypoints, wires deterministic PR validation, and proves the adoption before it is considered complete.

Adoption is not permission to redesign the product, replace native CI, weaken tests, or rewrite repository-specific invariants.

## Adoption trigger

When a user asks to apply, adopt, bootstrap, migrate to, or align a repository with the Engineering System:

1. Resolve the exact target repository and current branch/worktree.
2. Resolve the canonical Engineering System source from the supplied repository URL and identify an immutable canonical baseline SHA.
3. Read this standard and only the other standards needed for discovered conflicts.
4. Inventory the repository before writing files.
5. Prefer the deterministic adoption tool from the canonical Engineering System source for mechanical installation.
6. Keep semantic decisions visible and fail closed when they cannot be inferred safely.

## Link-only bootstrap and canonical source acquisition

The target repository does not initially contain `tools/adopt.py`. A link-only request therefore requires the agent to obtain the canonical Engineering System source first rather than inventing or reconstructing the helper.

Use one of these bounded approaches:

- read the canonical repository through an authenticated GitHub integration and materialize the required canonical files/tooling in a temporary workspace, or
- clone/fetch `datarelay-labs/engineering-system` into a separate temporary/tooling checkout.

Before applying changes, resolve and record the exact canonical commit SHA that will become the target repository's immutable `engineering_system.baseline`. Run the canonical `tools/adopt.py` from that canonical checkout against the target repository root.

Do not:
- copy an unpinned `main` snapshot into the product repository and call it the baseline
- assume the target repository already has the adoption helper
- modify the target merely to make the bootstrap tool available
- treat handbook/Athena content as a substitute for the canonical repository

After adoption, the target repository's generated entrypoints and pinned baseline are sufficient for normal lifecycle work; the target does not need to vendor the whole Engineering System.

## New or empty projects

A brand-new project still needs an explicit Git repository boundary before managed adoption because branch/HEAD/history are part of the safety and evidence model.

For a genuinely new repository with no executable product code yet:

1. establish or initialize the intended Git repository and origin/ownership boundary
2. run the same inventory/adoption flow
3. do not invent product architecture, frameworks, release commands, or operational contracts that the owner has not decided
4. use `--allow-no-tests` only when the repository genuinely has no executable test target yet
5. keep test/release/operations metadata conservative until real project-native commands exist
6. once executable implementation begins, establish real project-native tests and update the affected-test contract before treating normal development/release qualification as complete

`--allow-no-tests` is an explicit bootstrap state, not a permanent exemption from testing for a software project.

## Phase 1 — inventory

Record:

- repository root, origin, branch, HEAD, and dirty state
- languages/frameworks/package managers
- existing build/test/lint/typecheck commands and package-manager scripts
- existing CI workflows and release workflows
- source/test directory layout
- existing AI instruction surfaces such as AGENTS.md, CLAUDE.md, .cursorrules, .cursor/rules/**, .cursor/commands/**, and repository-specific agent files
- release/version/artifact sources
- product-specific architecture, security, persistence, migration, API, operational, and compatibility invariants
- production/deployment signals, runbook/incident expectations, and release/rollback ownership
- candidate domain boundaries for affected-test mapping
- whether repository checks are actually merge-blocking or only advisory

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
  --ci-mode shared \
  --test-command "<project-native affected/full test command>"
```

The tool installs only missing generated surfaces by default. It does not overwrite existing project files.

For repositories with a known release qualification command, also provide `--release-command`. Provide `--preflight-command` only when the command is a genuinely cheap deterministic release blocker.

For production-oriented repositories, pass `--operations-mode production`. The generated project profile then requires runbook/incident handling and the release profile requires operational E2E plus public smoke. If deployment signals exist while maturity is not clearly production/non-production, automatic mode fails closed for review instead of silently writing `production_oriented: false`.

The audit also reports discovered build/lint/typecheck commands and candidate domains. Override them only when repository evidence supports a better mapping.

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

For Engineering System >=1.6.0, the generated pull-request workflow also calls the pinned enforcement-reconciliation workflow. If live GitHub rulesets are visible during adoption, `merge_gate_status` is detected automatically; an explicit value that contradicts observable GitHub enforcement is rejected.

Production-oriented 1.6 adoption additionally requires:
- at least one repository runbook path
- a deterministic health command
- an operational E2E command and pass count
- a post-release public smoke command
- backup and restore-test commands when the project declares persistent state

Release automation is added when any release-contract command is configured. The generated release caller has separate `qualify` and `post-release` phases and is pinned to the same immutable Engineering System baseline.

## Test discovery rules

The adoption tool may auto-select a test command only when discovery is unambiguous.

Supported deterministic candidates include common Python, Node, Go, Rust, Maven, Gradle, and explicit repository test runners.

If multiple plausible commands exist or no safe command is found, adoption must stop for an explicit test command rather than inventing one.

Generated test metadata is conservative:

- changed source/test paths map to discovered domains rather than blindly forcing every path into `core`
- `--domain-test domain=command` may define truly domain-specific affected tests
- without domain-specific commands, one broad project-native test command may still cover all discovered domains and is reported as broad coverage
- discovered lint/typecheck/build commands become deterministic scenarios when safe
- a one-time `setup_command` may prepare dependencies before selected shared-CI scenarios
- cheap static PR guardrails remain separate
- ordinary PR adoption never turns a multi-hour release suite into a default PR gate

If the repository already has mature CI/runtime setup, prefer `ci_mode=native` unless the shared workflow is explicitly verified to provide equivalent environment setup.

## CI wiring

Managed adoption always wires adoption compliance on pull requests and immutable references to the canonical Engineering System baseline.

The project must select one CI mode after inventory:

- `shared` — use the Engineering System reusable affected-test workflow
- `native` — preserve an existing project-native CI workflow that already proves the affected-test invariant

If existing CI is detected, automatic mode fails closed until the agent explicitly chooses `--ci-mode shared` or `--ci-mode native`.

Native mode must record the specific workflow file(s) that own the affected-test invariant through `native_ci_workflows`; "some workflow exists" is not sufficient evidence. When several native workflows exist, adoption requires explicit `--native-ci-workflow` mapping.

Merge enforcement is recorded separately as `merge_gate_status: verified|advisory|unknown`. If the AI has GitHub ruleset/branch-protection visibility, it should verify required checks and record `verified`; otherwise it must not pretend that a workflow merely existing means merge is blocked.

Release preflight and release gate workflows are wired only when project-specific commands are explicitly known.

Do not duplicate a native gate that already proves the same invariant. In native mode, the generated Engineering System caller contains compliance only; the project-native workflow remains responsible for its mapped tests.

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
10. production/operations profile is explicitly resolved when deployment signals exist
11. native CI ownership is mapped to concrete workflow files when native mode is selected
12. merge-gate enforcement is reported as verified, advisory, or unknown rather than assumed

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

For the managed 1.5 -> 1.6 transition, use the deterministic helper:

```bash
python tools/upgrade-adoption.py --root /path/to/project --audit
```

Then apply only after required production/operations/release inputs are resolved:

```bash
python tools/upgrade-adoption.py \
  --root /path/to/project \
  --apply \
  --baseline-sha <canonical-1.6-sha>
```

The upgrade helper only rewrites known managed metadata/workflow surfaces and fails closed when it detects local/custom workflow changes. It does not rewrite Product Master/specification content or project-specific AI rules.

Managed upgrades also synchronize the canonical Cursor resume adapter (`.cursor/commands/resume.md`). When a known local alias such as `.cursor/commands/work-resume.md` is already present, the helper keeps that alias synchronized to the same canonical resume text. Existing resume adapters are replaced only when their content matches a known managed version; project-custom resume content fails closed for manual review.

Managed upgrades also synchronize known managed Engineering System version and immutable baseline SHA declarations in `AGENTS.md` and `README.md` when those exact managed forms are present. Surrounding project-specific text is preserved. Ambiguous or custom declaration forms fail closed for manual review rather than broad replacement.

## Organization-wide rollout

Use the deterministic org rollout helper to inventory an organization, classify repositories, and optionally open isolated per-repository rollout branches/PRs:

```bash
python tools/org-rollout.py --org <github-org> --audit
python tools/org-rollout.py --org <github-org> --apply --baseline-sha <canonical-sha>
```

Default mode is audit/dry-run and always resolves/compares the immutable canonical baseline. Archived repositories are reported as `SKIP_ARCHIVED` unless `--include-archived` is set. Apply mode reuses `tools/adopt.py` and `tools/upgrade-adoption.py`, never writes directly to default branches, and fails closed when adoption/upgrade inputs are ambiguous. A partial inventory must be reported as `ORG_ROLLOUT=PARTIAL` or `FAIL`, never as a global PASS. Per-repository checkout/clone failures are isolated as `ERROR`/`FAIL` results so the organization summary remains complete.

For a reviewed one-command apply, supply an optional versioned override manifest (`--override-manifest`) with repository-specific inputs that cannot be safely inferred (CI mode/native workflows, allow-no-tests, production/nonproduction, persistent-state, runbooks, health/backup/restore/release/E2E/smoke commands, and explicit exclusions). Missing required inputs still fail closed per repository.

Example override manifest:

```yaml
version: 1
defaults:
  ack_rule_review: true
  ci_mode: shared
repositories:
  example/skip-me:
    exclude: true
  example/needs-inputs:
    allow_no_tests: true
    operations_mode: nonproduction
```

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
