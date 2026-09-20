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
9. Drive the selected Work Packet forward sequentially until it reaches a terminal state or a genuine external blocker requires human action.
   - Execute the current Next Action and its required validation without expanding scope.
   - After each milestone, re-read/update the same Work Packet. If STATUS remains ACTIVE and the next action is executable without a new user decision, continue to that next action in the same session instead of returning a final "Done" response.
   - A successful intermediate milestone such as implementation PASS, commit, push, or PR creation is not workstream completion when CI/review/merge/closure remains.
   - When required CI or another machine-observable external condition is pending, actively monitor it at a reasonable interval (normally 30-60 seconds) and keep the CLI session in a working/waiting state. Print concise progress such as `WAITING_FOR_CI`; do not present a final completion summary while STATUS=ACTIVE.
   - If progress requires a human decision/approval, credentials, or another non-machine-resolvable action, update the packet with WAITING/BLOCKED evidence and the exact required action, then return a non-completion status. Do not claim Done.
   - If an external wait remains pending for about 30 minutes without a state change, record WAITING with fresh evidence and return without claiming completion; a later /resume continues from that durable state.
10. Follow affected-test-first and release-preflight rules. Never weaken valid tests, reuse different-HEAD evidence, or claim unexecuted work as PASS.
11. When the Work Packet explicitly references one or more non-`[AI Work]` product issues in the same TARGET_REPO, keep those issues synchronized at meaningful implementation milestones.
    - Do not rewrite or remove the product issue's problem statement, acceptance criteria, or non-goals.
    - Maintain one concise progress comment per product issue using the marker `<!-- ai-work-progress -->`; update that comment instead of appending repeated status comments.
    - Include: Work Packet number, branch, current HEAD or uncommitted state, implementation status, deterministic validation evidence, PR state/link when available, and the next action.
    - Update the product issue after implementation+validation, after PR creation or material CI/review changes, and after merge/closure.
    - Do not close the product issue merely because local implementation or validation passed. Close it only after the required integration/merge is complete, or when the Work Packet explicitly authorizes closure.
    - Do not infer unrelated issue links. Synchronize only issues explicitly identified by the active Work Packet.
12. Treat the Work Packet as terminally complete only when all completion conditions that apply to its scope are satisfied:
    - required implementation and validation are PASS
    - required commit/push/PR steps are complete
    - required CI/review gates are settled successfully
    - required integration/merge is complete
    - explicitly linked product issues that the packet expects to close are closed
    - no executable Next Action remains
    - packet STATUS is changed to DONE or COMPLETE
   While any of these remains pending, the workstream is not complete and the final response must not use "Done", "Task Completed", or equivalent completion wording.
13. At terminal completion, update and close/complete the same Work Packet rather than appending a new handoff:
    - STATUS=DONE or STATUS=COMPLETE
    - Current State
    - Next Action=NONE
    - Latest Evidence
    - Blockers=NONE
    - LAST_VERIFIED_HEAD
14. Keep the Work Packet concise. Link to commits/PRs/CI/canonical files instead of copying logs, specifications, prompts, or old conversation history.
15. Never place secrets, credentials, tokens, private keys, or unnecessary local absolute paths in the Work Packet.
