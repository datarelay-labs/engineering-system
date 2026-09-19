# Engineering System Adapters

The Engineering System core is intentionally independent of any single AI product.

## Core

Canonical behavior lives in:

- `standards/*`
- `.engineering/*` metadata contracts
- deterministic CI/release evidence rules

## Adapters

Adapters translate the core into tool-specific instruction surfaces:

| Adapter | Implementation |
|---|---|
| ChatGPT | `templates/CHATGPT_CUSTOM_INSTRUCTION.txt`, `templates/CHATGPT_PROJECT_INSTRUCTION.txt` |
| Cursor global | `templates/CURSOR_USER_RULE.txt` |
| Cursor repository | `templates/.cursor/rules/engineering-system.mdc` |
| Repository-neutral entrypoint | `templates/AGENTS.md` |
| GitHub enforcement | reusable workflows under `.github/workflows/` |
| GitHub session continuity | repository-scoped AI Work Packet Issue template |
| Cursor resume workflow | `templates/.cursor/commands/resume.md` |

Future tools may add adapters without changing the engineering lifecycle.

Session continuity is defined by `standards/SESSION_CONTINUITY.md`; adapters retrieve repository-scoped current state without copying conversation history.

## Rule

An adapter may:
- translate syntax
- identify tool-specific context-loading mechanisms
- integrate deterministic evidence

An adapter must not:
- weaken the core standard
- redefine canonical product behavior
- turn optional expensive validation into an ordinary per-change requirement
- create a competing source of truth
