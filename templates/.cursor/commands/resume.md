Resume the current engineering workstream from repository-scoped durable state.

Use maximum available reasoning/context.
Do not use parallel sub-agents.
Work sequentially in a single agent context.

1. Verify the local repository before doing anything:
   - git rev-parse --show-toplevel
   - git remote get-url origin
   - git branch --show-current
   - git rev-parse HEAD
   - git status --short --branch
2. Check for AGENTS.md and .engineering/project.yaml.
   - If both exist, read them first.
   - If either is missing, record ENGINEERING_SYSTEM_ADOPTION=ABSENT_OR_PENDING and continue under the canonical datarelay-labs/engineering-system default.
   - Missing local adoption files are not, by themselves, a reason to stop a valid resume.
   - Do not create, merge, or modify adoption files unless the active Work Packet explicitly authorizes that work.
3. Resolve the exact GitHub owner/repository from the current origin. Do not search other repositories after this point.
4. Retrieve open GitHub Issues whose title begins with `[AI Work]` using an available GitHub integration. An `ai-work` label may be used to narrow results but is optional. If no GitHub integration is available, use authenticated `gh`. If neither is available, STOP and report that GitHub Work Packet access must be configured; do not ask for a pasted historical handoff.
5. Select a packet only when:
   - TARGET_REPO exactly matches the current repository
   - STATUS=ACTIVE
   - BRANCH exactly matches the current branch when BRANCH is specified
6. Require exactly one match. If zero or multiple packets match, STOP and report the ambiguity/missing packet. Do not guess.
7. Treat LAST_VERIFIED_HEAD as advisory. Re-verify actual current branch/HEAD/dirty state, PR/CI status when relevant, and any repository facts needed for the task.
8. If present, read .engineering/tests.yaml only for implementation/debugging/testing and .engineering/release.yaml only for release/version/artifact work. If they are absent because adoption is pending, continue under the canonical Engineering System rules and the packet's explicit constraints. Read only canonical references needed for the packet's Next Action.
9. Execute only the current Next Action and its required validation. Do not expand scope.
10. Follow affected-test-first and release-preflight rules. Never weaken valid tests, reuse different-HEAD evidence, or claim unexecuted work as PASS.
11. At completion, update the same Work Packet rather than appending a new handoff:
    - Current State
    - Next Action
    - Latest Evidence
    - Blockers
    - LAST_VERIFIED_HEAD
12. Keep the Work Packet concise. Link to commits/PRs/CI/canonical files instead of copying logs, specifications, prompts, or old conversation history.
13. Never place secrets, credentials, tokens, private keys, or unnecessary local absolute paths in the Work Packet.
