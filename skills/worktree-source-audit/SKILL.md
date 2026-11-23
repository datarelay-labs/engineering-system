# Worktree and completed-source provenance audit

Use this skill only for an owner-authorized read-only Worktree/Issue/PR
reconciliation. Start with the repository's AGENTS.md, project.yaml and
actual Git main, and then read the declared skill resource if necessary.
The skill provides **no tool permission, identity, execution or merge authority**.

## Permission boundary

The skill itself is content only. The canonical repo_read envelope allows
only read actions; it does not grant shell or GitHub network calls. Reading
GitHub source or invoking local scripts needs the normal owner-selected
runtime's separately authorized existing tool classes. Never use this skill
to mint signed assertions or relax an ACL. A tool dispatcher must continue
to enforce the host-administered skills-contract authority. If unavailable,
fail closed rather than claiming authorization.

## Bounded audit

1. Resolve the exact repository and target checkout; inspect Git status,
   branch, HEAD, worktrees, current Issue and PR state.
2. Optionally run the **read-only** tool using its own authenticated GitHub
   session: python3 tools/lifecycle_audit.py --github-repo owner/repo
   --repo-root /absolute/primary-repository.
3. Treat OPEN COMPLETE with source PR CLOSED UNMERGED, ambiguous evidence,
   dirty/untracked worktrees, another open Issue/PR reference or active
   foreign processes as **PRESERVE / RECONCILE**. A COMPLETE string alone
   is not evidence of an integrated source.
4. Report only exact observed results. CANDIDATE_REVIEW is not authority to
   delete, close, force push, merge, deploy or release.
5. Actual terminal effects require the separate owner-authorized
   worktree_lifecycle.py read-only plan and a second digest-bound --apply.
   Never auto-execute --apply from this skill.

## Repeatable validation

Run tests/test_lifecycle_audit.py equivalent at
tools/test_lifecycle_audit.py and tools/test_worktree_lifecycle.py from
the canonical checkout. A real user E2E observation is independent from
source/Worktree closure; do not mark it PASS based on CLI test receipts.
