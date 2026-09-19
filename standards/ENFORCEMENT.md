# Engineering System Enforcement

The Engineering System is useful only if agents and CI actually consume it.

## Enforcement layers

### 1. Repository entrypoint
Every adopted repository must contain:
- `AGENTS.md`
- `.engineering/project.yaml`
- `.engineering/tests.yaml`
- `.engineering/release.yaml`

An AI agent must read these before making development changes.

### 2. Cursor persistent rule
Every adopted repository must contain:

```text
.cursor/rules/engineering-system.mdc
```

The rule must use `alwaysApply: true` so Cursor Agent receives the engineering instructions in every chat for that repository.

The rule must require Cursor to read the repository-local engineering metadata before implementation and to fail closed when required context is unavailable.

### 3. Deterministic CI compliance
Adopted repositories should call the shared `adoption-compliance.yml` workflow. It validates that required files exist and that the project declares an Engineering System version.

Compliance is a required gate candidate for protected branches after the reference implementation is proven.

### 4. Work evidence
A development task is not complete merely because an agent states that it followed the standard. Evidence is the applicable combination of:
- affected tests
- deterministic regression
- lifecycle/platform validation
- release qualification
- exact source identity

### 5. Human / ChatGPT entry rule
For Data Relay Labs development work, ChatGPT or another independent reviewer should first read the target repository's `AGENTS.md` and `.engineering/` profiles and then the pinned Engineering System baseline when needed.

If repository context is unavailable, the assistant must not pretend to have applied repository-specific engineering rules.

## Fail-closed behavior

For implementation/release work, missing mandatory engineering context is a configuration defect. Do not silently continue as if the repository were adopted correctly.

## What is not enforceable by Git alone

GitHub cannot force a generic external chat product to read a repository before answering. That behavior must be supplied through the assistant/project/user instruction layer.

For this reason the repository includes `templates/CHATGPT_CUSTOM_INSTRUCTION.txt` for the human owner to apply once at the account/project instruction level.
