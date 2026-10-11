# Owner Delivery Profile — DataRelay and XDR

> Owner-scoped delivery allocation (2026-10-11). Not a universal managed baseline
> and not a new runtime/profile/safety authority. Canonical repository:
> `datarelay-labs/engineering-system`. Generic contract: `standards/CORE.md`
> and `standards/ADOPTION.md`.

## Scope and accountable executor

| Repository scope | Engineering System steward | Repository/project chat |
| --- | --- | --- |
| `datarelay-labs/engineering-system` | Owns shared engineering regulations, schemas, common templates/tools, adoption contracts, deterministic policy tests and systemic supervision; implements these in this repository | No consumer-product role |
| `datarelay-labs/*` product repositories (Control, Link, Grant, Atlas, Product Foundation, Link plugin), **except explicitly excluded repos** | Defines portable rules and compatibility/acceptance; **writes the existing project GitHub roadmap** and verifies its persistence. Does **not** implement the product's feature/UI, local installation, E2E, or ordinary PR/CI as a surrogate | **Directly implements** its own roadmap, affected tests, browser/CLI/user E2E, normal Git operations, PR/CI/review and release readiness |
| `datarelay-labs/*-docs` and shared documentation repos | Defines shared documentation policy; records required updates in the relevant owning product/docs roadmap, without opportunistic edits | Owning product/docs chat performs documentation source changes and publication qualification |
| `xdr-labs/*` **except `xdr-labs/athena`**, including code, documentation, portal, mirror and DP OS upgrade repos | **Direct executor**, not roadmap-only: checks current repo/branch/HEAD/worktree and active work; makes permitted changes, tests, commits/pushes, PR/CI/review and adoption proof itself | May assist only when explicitly assigned by the owner; no mandatory separate product-chat handoff |

## Explicit exclusions — owner directive 2026-10-11

- `datarelay-labs/athena` — **EXCLUDED**, owner plans to delete this repository.
- `xdr-labs/athena` — **EXCLUDED**, owner plans to delete this repository.

Neither repository is an Engineering System adoption, governance rollout,
product-Chat delegation, XDR direct-implementation, CI/PR, or E2E target.
Do **not** resume or create Athena work even when wildcard organization
discovery lists it. Previously created Athena Draft PRs are to be **closed,
not merged**; their commits/branches and original repositories are not
deleted by this exclusion update. The owner may delete the repositories
separately. No OAuth `workflow` authorization or Actions activation is
required for excluded Athena.

Current execution surface is the authorized ChatGPT Chat runtime under the existing
execution profile, unless the owner explicitly chooses a different implementer.
The explicit owner request outranks this allocation for a named repository/task.

## DataRelay roadmap delivery contract

1. Exclude repositories listed above **before** roadmap or Work Packet discovery. For in-scope repositories, resolve the **existing product roadmap Issue** and any current ACTIVE Work Packet.
   Add a bounded adoption tranche to the roadmap; do not replace its first unfinished
   product action, rewrite a dirty worktree, spawn a competing Work Packet or create a
   duplicate roadmap.
2. Each tranche includes: canonical Engineering System reference (exact SHA/PR and
   merge state), required behavior, affected package/config/docs, project compatibility
   considerations, implementation owner (**product chat**), tests and applicable
   Surface Reconciliation/Full User E2E, PR/CI/review evidence, and completion criteria.
3. Persist via authenticated GitHub, then independently re-read the exact Issue.
   A successful Issue write proves only **roadmap handoff**, never product adoption.
4. Each product chat resumes **its own prior unfinished Work Packet** first; it
   implements the adoption tranche at its scheduling priority without losing existing
   owner/roadmap priorities. It updates its own GitHub/Git/test state to completion.
5. The Engineering System steward may read product evidence to audit conformance,
   and may repair minimal coordination state for a demonstrable systemic failure;
   it does not take over healthy product implementation.

## XDR direct-application contract

1. Enumerate the owner's applicable `xdr-labs` repositories **excluding the exact repository IDs above** and record each repo's
   branch, HEAD, dirty files, existing Engineering System baseline and stricter rules.
2. Apply the new delivery ownership entry in that repository's **existing** rules
   without discarding its native tests, guardrails, in-flight branches/PRs or changes.
   For policy-only changes, use isolated worktrees/branches where needed.
3. Execute each repository's cheapest affected validation and normal Git/PR/CI
   proof. A queued/failed check is not PASS. Never auto-merge, publish, mutate
   production, change credentials/permissions, or bypass safety inspection.
4. Record exact per-repository results; if one is genuinely blocked, continue
   other independent XDR repositories, retaining blockers and owner actions truthfully.

## Boundary acceptance

- Canonical common rules are reviewable and do not hardwire the DataRelay or XDR
  organization names into universal consumer templates.
- Explicitly excluded repositories never receive implementation or delegated roadmap work, even when matched by an organization wildcard.
- DataRelay products receive persisted, project-specific GitHub roadmap tranches
  and execute them **in their own project chats**; the steward makes no consumer
  product source/PR mutation merely for delegation.
- XDR receives **actual** repository-local policy changes and Git/CI evidence
  directly from the Engineering System steward.
- Prior explicit safety denials remain binding. Any high-risk release/merge,
  production, destructive, credential, or permission effect retains its own gates.
- A delegated roadmap, passing policy unit tests or a draft PR is not evidence
  of an implemented consumer product or an authorized release.
