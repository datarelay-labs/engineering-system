# Quality Standard

Quality covers correctness, regression safety, usability, compatibility, resilience, and release evidence.

## Required quality loop

```text
design gate when needed
 -> change
 -> build/lint/typecheck when applicable
 -> affected tests
 -> regression
 -> wider qualification by risk
 -> evidence
```

Use existing project-native build/lint/typecheck commands when they are meaningful. The Engineering System does not require adding a new tool merely to fill a checklist.

## Test model

Use the L0-L8 model defined in `TESTING.md`. Projects choose applicable levels but must not skip a required level silently.

## Defect handling

For BUGFIX work:
1. reproduce the defect
2. create or identify a regression that exposes it when practical
3. implement the smallest correct fix or coherent fix batch
4. prove the focused regression passes
5. run the cheapest affected/invalidated tests
6. repeat steps 1-5 for remaining actionable findings without restarting an unrelated full suite
7. widen to broad/full regression only after affected convergence is clean, or earlier when dependency/invalidation uncertainty or risk requires it
8. retain the regression permanently
9. update runbook/ADR only when the defect reveals an operational or architectural gap

## Flaky tests

A flaky required test is a defect, not a reason to ignore the gate.
- do not repeatedly rerun until green and call it PASS
- capture the failure evidence
- quarantine only when the project explicitly records owner, reason, and restoration criteria
- release-critical coverage must have a deterministic replacement before quarantine is accepted

## Compatibility quality

When a change touches public API, CLI, configuration, data format, installer behavior, or persisted state:
- identify the compatibility contract
- test old-to-new paths when supported
- test rejection/error behavior for unsupported combinations
- update deprecation/upgrade documentation when behavior changes

## Human UX quality

Applicable user-facing products must test success paths plus misuse and recovery:
- invalid input
- wrong context
- copy/paste
- cancel / EOF / Ctrl+C
- stale/generated guidance
- cross-output consistency

For release qualification, this is not satisfied by component/API coverage alone. A user-facing product requires the two human-equivalent gates defined in `TESTING.md`: Surface Reconciliation and Full User E2E. Browser products must execute the real rendered UI in Chromium/Chrome; Playwright may drive the browser, but jsdom, mocked component rendering, API mutation, or static contract checks cannot stand in for the real user action.

A release finding is based on the user's observable mission and outcome. If the implementation passes an internal/API check while the actual public user action is missing, undiscoverable, misleading, blocked, or unrecoverable, the user-facing gate fails.

## Change risk and verification depth

Change kind describes what is being changed; change risk determines verification depth. Risk does **not** determine roadmap priority, and it does not automatically require a second model/provider actor.

Use `CHANGE_RISK=LOW|MEDIUM|HIGH|CRITICAL` as a compact coordinator signal, based on the combination of:
- production reach/blast radius;
- security/privilege/trust-boundary impact;
- persisted-data or migration impact;
- public API/CLI/config/compatibility impact;
- reversibility/rollback quality;
- cross-service or shared-infrastructure scope.

Default behavior:

- `LOW` — targeted deterministic validation and normal diff/review.
- `MEDIUM` — affected regression plus normal independent review where configured.
- `HIGH` — selected-runtime exact-HEAD self-audit plus wider affected qualification and explicit rollback/compatibility/security evidence as applicable. Current deterministic tests, CI/review state when applicable, and actionable-finding disposition are required; self-report alone is not evidence.
- `CRITICAL` — HIGH requirements plus explicit human approval for destructive/external/prod execution when required by the security/release contract and release/operational evidence appropriate to the change.

These are verification-depth defaults, not a replacement for task-specific mandatory gates. A seemingly small diff may be HIGH/CRITICAL if its blast radius or irreversibility is large.

Under the selected execution profile, the selected implementation runtime may also perform terminal audit when it re-reads current repository/Work Packet/PR state and bases PASS on exact-HEAD machine-observable evidence rather than its own prior claims. Provider/runtime selection is profile data, not a core invariant. A distinct reviewer/runtime or `python3 tools/independent_verifier.py verify` remains optional defense-in-depth or escalation. If evidence is explicitly presented as independent verifier evidence, its identity and context must be distinct from the implementer. Independent-review runtime availability or quota alone must not block otherwise satisfied completion gates.

## Trust by evidence

Optional `.engineering/verification.yaml` maps a feature to applicable domains and to launch, drive, observe, and cleanup references. Those references are existing test scenario IDs, runtime authority or capability IDs, and skill or profile IDs only. The map contains no command, shell, URL, endpoint, or argv fields. `python3 tools/verification-contract.py check` validates a present map and passes when the file is absent. Adoption never creates the file.

`python3 tools/verification-contract.py assess` reads one bounded evidence receipt and no boundary file. Receipt-only CLI output stays at T0/BLOCK. Implementer-produced output is never terminal evidence. T1–T5 calculation requires an in-process `TrustedCoordinatorBoundary` passed to `assess`. A raw dict or parsed JSON object stays at T0. The tool does not establish that precondition, does not verify skills-contract signatures, and a provenance string is not a trust anchor. Receipts bind repository, workstream, intent revision `>= 1`, and a lowercase 40-hex HEAD. The assessor does not execute project commands and does not merge, release, or deploy.

- T0 is self-report or receipt-only CLI output and is never completion evidence.
- T1 is referenced deterministic test, skill, and profile evidence at the exact HEAD and intent revision, only from the coordinator precondition.
- T2 adds exact-HEAD CI evidence from that same precondition.
- T3 adds PASS evidence for every referenced runtime id plus a bounded runtime subject. Missing runtime evidence cannot reach T3.
- T4 calls `independent_verifier.evaluate` on that coordinator precondition for the exact subject. Receipt-minted verifier actors cannot satisfy T4.
- T5 reports `AUTOMATION_ELIGIBLE=YES` only after T4 and coordinator policy eligibility outside the receipt, plus an explicit `automation_eligible` entry. `EXTERNAL_MUTATION=NO`. `external_digest` is not treated as verified content; `DIGEST_VERIFIED=NO`. Eligibility does not authorize merge, release, deploy, or external writes.

Stale HEAD, stale intent, unknown evidence, and unbounded logs fail closed. UNKNOWN stays BLOCK and never PASS.

Conditional auto-merge eligibility is a post-T5 policy gate implemented by `tools/auto_merge_eligibility.py`. The public `evaluate --request-json` CLI accepts bounded facts for validation/denial only and never receives a trust boundary, so caller JSON cannot produce `ELIGIBLE`. Positive in-process evaluation requires the exact `TrustedCoordinatorBoundary` type from `verification-contract.py` and passes the request's bounded verification receipt + manifest through `verification-contract.assess()` for the same repository, workstream, intent revision, and HEAD. Raw dictionaries, JSON round-trips, forged T5 labels, an empty/non-T5 boundary, or a failed assessor result stay `BLOCK`. Only an actual assessor result of `PASS` + `T5` + `AUTOMATION_ELIGIBLE=YES` can enter the remaining repository/profile/task/risk, CI/review/thread, and mutation/replay policy gates. `ELIGIBLE` is evidence-only and always carries `authorizes_merge=false`, `external_mutation=false`, `executes_commands=false`, and `performs_network_io=false`. Missing, unknown, stale, replayed, ambiguous, disallowed, malformed, or failing facts return `BLOCK` or `DENY`. Actual merge execution is outside this contract and requires separately authorized mutation authority.

## Bounded hardening and audit depth

Security, reliability, quality, and architecture can always be improved further. A quality process therefore needs a stopping rule as well as a defect-finding rule.

- Translate open-ended requests such as “harden this,” “audit everything,” or “make it robust” into a finite threat/risk surface and explicit completion contract before implementation.
- Required risk classes and known concrete failures belong in the current packet; speculative defense-in-depth and optional improvements become follow-up work once the required oracle is green.
- Do not treat the number of newly discovered low-severity opportunities as evidence that the current packet should remain active forever.
- After the completion contract passes and all review required by the task/risk contract has no blocking defect, additional broad/deeper audit is a new scope decision.
- Apply the sufficiency/depth-budget contract in `SESSION_CONTINUITY.md`; exceptions require concrete safety, data-integrity, incident, or release-blocking evidence.

## Architecture and quality entropy

Higher agent throughput must not be allowed to multiply local inconsistencies.

For repositories with meaningful architectural boundaries:
- encode important layering/dependency/public-boundary invariants as deterministic checks when practical;
- when one public contract has multiple representations, prefer one explicit authority plus generation or deterministic parity checks instead of independently maintained copies;
- model non-local derived-state dependencies with direct affected-test invalidation edges when domain mapping alone cannot guarantee closure;
- for user-facing changes, run a bounded Surface Delta Reconciliation during development/PR so ordinary discoverability/workflow drift is found close to the introducing change rather than first at release;
- prefer existing canonical abstractions over near-duplicate helpers/frameworks;
- treat repeated exceptions, duplicated patterns, boundary violations, and stale compatibility shims as quality debt;
- periodically convert accumulated drift into small, targeted cleanup Work Packets/PRs rather than mixing broad cleanup into feature work;
- do not use generated LOC or number of cleanups as the quality metric; use reduced violations, rework, regressions, and review friction.

## Performance and resilience

Use:
```text
steady state -> baseline -> load -> concurrent/mixed operation -> fault -> recovery -> steady state
```

Do not invent arbitrary thresholds. Use documented requirements/SLOs when present and always enforce safety invariants.

Canonical testing details are in `TESTING.md`.

## Historical benchmark fixtures

The seven mandatory historical cases freeze in `evals/benchmark/fixtures.yaml`. `python3 tools/benchmark_fixture.py validate` checks that manifest. Each runnable case has an immutable commit identity, a sanitized worker-visible task, a separate predeclared oracle, fresh-worktree reset metadata, required evidence classes, and machine-checkable secret and production-mutation exclusions. The worker task is the only Phase-B objective input. It does not include the oracle, outcome lineage, or the mutable source issue. Incident diagnosis includes the frozen preserved-evidence facts and omits the root-cause conclusion. The multi-repository task includes the frozen 2026-09-23T03:29:00Z open rollout snapshot instead of live organization discovery. Worker-visible topology is input only: repository, pull request, role, snapshot head, and OPEN state. It omits merge outcomes and mutable source records. A case whose source is still moving stays `BLOCKED` with a stable reason and no commit identity. Control and candidate anchors are exact SHAs. Result field `FIXTURE_ID` is `<manifest git sha>:<case id>`. `bind_fixture_id` checks that id against the supplied 40-hex commit of the manifest. A task, oracle, or source edit changes that commit, so an older id does not bind. The id is not a digest of the manifest. Result fields stay the side-by-side #44 record. The manifest has no aggregate or weighted score. Validation does not run a control or candidate model benchmark.

`python3 tools/benchmark_execution.py dry-run` prepares the frozen BENCH-BUG-001 pilot only. It binds the control head, candidate head, manifest head, fixture id, and task-source head, then gives each lane the sanitized `worker_task()` projection plus run metadata. The lane artifact is a non-final result template: evaluator-owned #44 fields stay null, and unavailable wall time and model cost stay `UNKNOWN`. Observed duration, usage, counts, and exact-head evidence are derived only from a schema-valid canonical efficiency telemetry record whose repository, workstream, run id, and profile match the expected lane, and whose `validation.exact_head` matches that lane's system head whenever exact-head evidence is present. A dry-run may stamp the frozen manifest revision only when the loaded manifest content is the blob at that revision. A final BENCH-BUG-001 result accepts only that frozen fixture and either the control version/head pair or the candidate version/head pair. #44 `REVIEW_REWORK` uses `efficiency_telemetry.rework_count`, the same P0b total of `pr_rework`, `ci_rework`, and `review_rework` that an outcome report records as `rework_count`. The dry-run does not emit a second telemetry record. A provider, model, reasoning, or toolset mismatch is `PARTIAL` or `BLOCK`. The template is not a final benchmark result. The dry-run does not launch a model or agent worker.
