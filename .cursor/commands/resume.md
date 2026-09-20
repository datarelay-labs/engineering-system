Resume the current engineering workstream from repository-scoped durable state.

Use maximum available reasoning/context.
Do not use parallel sub-agents.
Work sequentially in a single agent context.

1. Verify that the required local execution environment is usable before doing anything.
   - If the shell/process runner cannot start, or Git cannot execute, STOP with `ENVIRONMENT_BLOCKER`.
   - Do not probe Athena, unrelated repositories, old agent transcripts, or other knowledge systems as a substitute for local repository identity.
   - GitHub may provide coordination context, but it does not replace local Git/worktree verification for implementation work.
2. Verify the local repository:
   - git rev-parse --show-toplevel
   - git remote get-url origin
   - git branch --show-current
   - git rev-parse HEAD
   - git status --short --branch
3. Check for AGENTS.md and .engineering/project.yaml.
   - If both exist, read them first.
   - If either is missing, record ENGINEERING_SYSTEM_ADOPTION=ABSENT_OR_PENDING and continue under the canonical datarelay-labs/engineering-system default.
   - Missing local adoption files are not, by themselves, a reason to stop a valid resume.
   - Do not create, merge, or modify adoption files unless the active Work Packet explicitly authorizes that work.
4. Resolve the exact GitHub owner/repository from the current origin. Do not search other repositories after this point.
5. Retrieve open GitHub Issues whose title begins with `[AI Work]` using an available GitHub integration. An `ai-work` label may be used to narrow results but is optional. If no GitHub integration is available, use authenticated `gh`. If neither is available, STOP and report that GitHub Work Packet access must be configured; do not ask for a pasted historical handoff.
6. Select a packet only when:
   - TARGET_REPO exactly matches the current repository
   - STATUS=ACTIVE
   - BRANCH exactly matches the current branch when BRANCH is specified
7. Require exactly one match. If zero or multiple packets match, STOP and report the ambiguity/missing packet. Do not guess.
8. Validate the selected packet contract before execution:
   - Canonical statuses are only ACTIVE, PAUSED, BLOCKED, COMPLETE. Do not accept invented aliases such as CURSOR_READY, WAITING, or DONE.
   - For PACKET_VERSION>=2, require TASK_KIND and OWNER_INTENT.
   - Confirm that Next Action directly advances both Goal and OWNER_INTENT and is compatible with TASK_KIND.
   - If those fields materially disagree, STOP with WORK_PACKET_SCOPE_MISMATCH. Do not search unrelated chats, Athena, or other repositories to reinterpret the packet.
   - PACKET_VERSION=1 remains legacy-compatible; do not block solely because TASK_KIND/OWNER_INTENT are absent, but migrate to v2 on the next meaningful update.
9. Treat LAST_VERIFIED_HEAD as advisory. Re-verify actual current branch/HEAD/dirty state, PR/CI status when relevant, and any repository facts needed for the task.
10. If present, read .engineering/tests.yaml only for implementation/debugging/testing and .engineering/release.yaml only for release/version/artifact work. If they are absent because adoption is pending, continue under the canonical Engineering System rules and the packet's explicit constraints. Read only canonical references needed for the packet's Next Action.
11. Drive the selected Work Packet forward sequentially until it reaches a terminal state or a genuine external blocker requires human action.
   - Execute the current Next Action and its required validation without expanding scope.
   - After each milestone, re-read/update the same Work Packet. If STATUS remains ACTIVE and the next action is executable without a new user decision, continue to that next action in the same session instead of returning a final "Done" response.
   - A successful intermediate milestone such as implementation PASS, commit, push, or PR creation is not workstream completion when CI/review/merge/closure remains.
   - Before merge or terminal completion, inspect the current PR's machine-observable review submissions, top-level comments, and inline review feedback.
   - Treat every actionable finding from a human reviewer or configured automated reviewer as an executable Next Action. A COMMENTED/advisory review state is not itself PASS or FAIL; inspect the content.
   - Resolve each actionable finding by fixing it and rerunning affected validation, or by recording a concise evidence-backed disposition explaining why it is non-actionable, out of scope, or incorrect. Do not merge or claim terminal completion while actionable review feedback remains unaddressed.
   - When required CI or another machine-observable external condition is pending, actively monitor it at a reasonable interval (normally 30-60 seconds) and keep the CLI session in a working/waiting state. Print concise progress such as `WAITING_FOR_CI`; do not present a final completion summary while STATUS=ACTIVE.
   - If progress requires a human decision/approval, credentials, or another non-machine-resolvable action, set STATUS=BLOCKED, record the exact required action, then return a non-completion status.
   - If a machine-observable external wait remains pending for about 30 minutes without a state change, keep STATUS=ACTIVE, record `WAITING_FOR_<CONDITION>` in Current State/Latest Evidence, and return without claiming completion; a later /resume continues from that durable state.
12. Follow affected-test-first and release-preflight rules. Never weaken valid tests, reuse different-HEAD evidence, or claim unexecuted work as PASS.
13. When the Work Packet explicitly references one or more non-`[AI Work]` product issues in the same TARGET_REPO, keep those issues synchronized at meaningful implementation milestones.
    - Do not rewrite or remove the product issue's problem statement, acceptance criteria, or non-goals.
    - Maintain one concise progress comment per product issue using the marker `<!-- ai-work-progress -->`; update that comment instead of appending repeated status comments.
    - Include: Work Packet number, branch, current HEAD or uncommitted state, implementation status, deterministic validation evidence, PR state/link when available, and the next action.
    - Update the product issue after implementation+validation, after PR creation or material CI/review changes, and after merge/closure.
    - Do not close the product issue merely because local implementation or validation passed. Close it only after the required integration/merge is complete, or when the Work Packet explicitly authorizes closure.
    - Do not infer unrelated issue links. Synchronize only issues explicitly identified by the active Work Packet.
14. Treat the Work Packet as terminally complete only when all completion conditions that apply to its scope are satisfied:
    - required implementation and validation are PASS
    - required commit/push/PR steps are complete
    - required CI/review gates are settled successfully
    - no actionable PR review feedback remains unaddressed
    - required integration/merge is complete
    - explicitly linked product issues that the packet expects to close are closed
    - no executable Next Action remains
    - packet STATUS is changed to COMPLETE
   While any of these remains pending, the workstream is not complete and the final response must not use "Done", "Task Completed", or equivalent completion wording.
15. At terminal completion, update and close/complete the same Work Packet rather than appending a new handoff:
    - STATUS=COMPLETE
    - Current State
    - Next Action=NONE
    - Latest Evidence
    - Blockers=NONE
    - LAST_VERIFIED_HEAD
16. Keep the Work Packet concise. Link to commits/PRs/CI/canonical files instead of copying logs, specifications, prompts, or old conversation history.
17. Never place secrets, credentials, tokens, private keys, or unnecessary local absolute paths in the Work Packet.
