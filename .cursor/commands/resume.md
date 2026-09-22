Resume the current engineering workstream from repository-scoped durable state.

Use minimum sufficient context and reasoning. Do not request maximum reasoning by default. Work sequentially; do not use parallel sub-agents unless the active task explicitly requires them.

1. Verify the local execution environment and Git identity:
   - `git rev-parse --show-toplevel`
   - `git remote get-url origin`
   - `git branch --show-current`
   - `git rev-parse HEAD`
   - `git status --short --branch`
   If shell/Git cannot run, stop with `ENVIRONMENT_BLOCKER`.

2. Read `AGENTS.md` and `.engineering/project.yaml` when present. If adoption files are missing, record `ENGINEERING_SYSTEM_ADOPTION=ABSENT_OR_PENDING` and continue under the canonical Engineering System unless the packet explicitly requires adoption work.

3. Resolve the exact GitHub repository from origin. Read only open Issues titled `[AI Work] ...` in that repository and require exactly one match where:
   - `TARGET_REPO` matches exactly
   - `STATUS=ACTIVE`
   - `BRANCH` matches the current branch when specified
   Zero or multiple matches are a fail-closed stop.

4. Validate the packet before execution:
   - statuses are only ACTIVE, PAUSED, BLOCKED, COMPLETE
   - packet v2 requires TASK_KIND and OWNER_INTENT
   - Next Action must directly advance Goal and OWNER_INTENT and fit TASK_KIND
   - otherwise stop with `WORK_PACKET_SCOPE_MISMATCH`

5. Re-verify actual branch/HEAD/dirty state and PR state when relevant. Treat LAST_VERIFIED_HEAD as advisory. Load `.engineering/tests.yaml`, `.engineering/release.yaml`, and canonical references only when needed for the current Next Action.

6. Execute the current bounded local/deterministic phase without expanding scope. Use the smallest correct change and cheapest affected validation first. After a meaningful milestone, update the same Work Packet with concise current state, exact evidence, and the next action.

7. Do not keep the AI coding session alive polling CI, review, deployment, or another machine-observable external condition.
   - If such a wait is pending, keep `STATUS=ACTIVE`, record `WAITING_FOR_<CONDITION>` plus the observable reference in Current State/Latest Evidence, set the resumable Next Action, and return control to coordinator/automation.
   - If progress requires a human decision, approval, credential, or other non-machine-resolvable action, set `STATUS=BLOCKED` and record the exact required action.
   - A later resume must re-check the external state rather than replay old logs.

8. Before merge or terminal completion, inspect current actionable review feedback. Fix/revalidate every actionable finding or record a concise evidence-backed disposition.

9. Complete the packet only when all scope-applicable implementation, validation, commit/push/PR, CI/review, integration/merge, and explicitly linked issue conditions are settled and no executable Next Action remains. Then set `STATUS=COMPLETE`, `Next Action=NONE`, fresh evidence, `Blockers=NONE`, and current LAST_VERIFIED_HEAD. Otherwise do not claim completion.

10. Keep the Work Packet small. Link to commits/PRs/CI/canonical files instead of copying logs, specs, prompts, or conversation history. Never store secrets.
