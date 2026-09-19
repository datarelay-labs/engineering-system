# Engineering System Enforcement

The Engineering System is useful only if agents and CI actually consume it.

## Enforcement layers

### 1. Repository entrypoint

Every adopted repository must contain:
- `AGENTS.md`
- `.engineering/project.yaml`
- `.engineering/tests.yaml`
- `.engineering/release.yaml`

An AI agent must read these before making development, debugging, test, release, upgrade, operations, incident, or documentation changes.

### 2. Cursor persistent rule

Every adopted repository must contain `.cursor/rules/engineering-system.mdc` with `alwaysApply: true`.

The rule must require Cursor to load repository engineering metadata before implementation and fail closed when required context is unavailable.

### 3. Cursor user-level bootstrap

Because a repository rule cannot help before a repository is adopted, the owner should add `templates/CURSOR_USER_RULE.txt` once to Cursor User Rules. Its purpose is to make missing Engineering System adoption visible instead of silently proceeding.

### 4. ChatGPT project/account bootstrap

Git cannot inject instructions into an unrelated ChatGPT conversation. The owner should add `templates/CHATGPT_PROJECT_INSTRUCTION.txt` to the ChatGPT project used for Data Relay Labs engineering work. `templates/CHATGPT_CUSTOM_INSTRUCTION.txt` is the shorter account-wide fallback.

ChatGPT must not claim repository-specific compliance unless it has actually read the target repository engineering context.

### 5. Deterministic CI compliance

Adopted repositories should call the shared `adoption-compliance.yml` workflow. It validates that required files exist, the Cursor rule is always applied, and the repository declares an Engineering System version.

After the reference implementation is proven, this compliance check should be configured as a GitHub required status check for protected integration/release branches.

### 6. Work evidence

A task is not complete merely because an agent states that it followed the standard. Evidence is the applicable combination of:
- affected tests
- regression evidence
- security/compatibility validation
- lifecycle/platform validation
- release qualification
- exact source/artifact identity

## Fail-closed behavior

For implementation/release work, missing mandatory engineering context is a configuration defect. Do not silently continue as if the repository were adopted correctly.

## Enforcement boundary

Repository files and GitHub CI can mechanically enforce repository structure and merge gates. External AI products require their project/user instruction layer to bootstrap repository reading. The combination is intentional:

global/project AI instruction -> repository AGENTS/rules -> .engineering metadata -> pinned Engineering System -> deterministic GitHub gates