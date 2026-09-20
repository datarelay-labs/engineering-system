# Engineering System Enforcement

The Engineering System is useful only if agents and CI actually consume it.

## Applicability

The global default applies to every software/product engineering project the owner works on with AI assistance, regardless of GitHub organization, repository owner, product name, or project location.

The canonical standard lives in `datarelay-labs/engineering-system`.

## Core vs adapters

The Engineering System core is agent-agnostic:
- `standards/*`
- `.engineering/*` contract
- deterministic GitHub gates
- evidence and release rules

Tool-specific behavior is implemented through adapters:
- ChatGPT Custom/Project Instruction
- Cursor User Rule
- repository `.cursor/rules/engineering-system.mdc`
- future AI-tool adapters

An adapter may translate the core rules into tool-specific instructions but must not weaken them.

## Context-loading enforcement

Always read:
- `AGENTS.md`
- `.engineering/project.yaml`

Read only when relevant:
- `.engineering/tests.yaml` for implementation/debugging/testing
- `.engineering/release.yaml` for release/version/artifact work
- the relevant Engineering System standard(s)
- relevant product specification/ADR/runbook material

Do not preload every standard, Wiki page, archive, or historical discussion.

## Repository entrypoint

Every adopted repository must contain:
- `AGENTS.md`
- `.engineering/project.yaml`
- `.engineering/tests.yaml`
- `.engineering/release.yaml`
- `.cursor/rules/engineering-system.mdc`

Engineering System >=1.3.0 additionally requires:
- `.cursor/commands/resume.md`
- `.github/ISSUE_TEMPLATE/ai-work-packet.md`

Engineering System >=1.4.0 managed adoption additionally requires:
- immutable `engineering_system.baseline`
- `.github/workflows/engineering-system.yml` pinned to that baseline

Engineering System >=1.5.0 managed adoption additionally records:
- explicit operations posture (production or non-production)
- incident/runbook requirements for production-oriented projects
- concrete native CI workflow ownership when `ci_mode=native`
- merge-gate enforcement state as `verified`, `advisory`, or `unknown`
- design/incident routing in the repository AGENTS entrypoint

Engineering System >=1.6.0 managed adoption additionally:
- wires `.github/workflows/enforcement-check.yml` through the pinned baseline
- compares declared merge-gate state with observable live GitHub rulesets on pull requests
- treats a declared `verified` state that cannot be verified as a failure
- treats observable `verified` vs `advisory` drift as a configuration failure
- permits `unknown` when GitHub enforcement visibility genuinely is unavailable

Use `standards/ADOPTION.md` and `tools/adopt.py` for managed adoption. Existing repository rules must be classified before destructive cleanup.

## Deterministic CI compliance

Adopted repositories should validate required Engineering System files and use fast affected PR checks.

Shared workflows must not force duplicate project-native qualification. Expensive full-suite/lifecycle/platform/performance/operational gates belong at the appropriate release boundary.

## Fail-closed behavior

Missing or contradictory mandatory engineering context is a configuration defect. Do not silently continue as if repository-specific compliance had been established.

## Enforcement chain

```text
global AI adapter
 -> repository AGENTS / tool adapter
 -> minimal relevant .engineering metadata
 -> pinned Engineering System
 -> deterministic affected checks
 -> release preflight
 -> exact-candidate qualification
```


## Live GitHub enforcement reconciliation

Workflow existence is not evidence that merge blocking is active.

For 1.6+ adopted repositories:

```text
project.yaml merge_gate_status
        +
live default-branch GitHub rulesets
        ↓
reconciliation
```

`verified` means an active default-branch rule requires both pull requests and one or more status checks. `advisory` means the repository automation may run but those merge gates are not both enforced. `unknown` is reserved for cases where live enforcement cannot be observed safely.

The reconciliation workflow is read-only. The Engineering System does not silently create or relax GitHub administrative rulesets.
