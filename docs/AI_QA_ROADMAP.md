# AI QA & E2E Quality Factory roadmap

Workstream: [Engineering System Issue #317](https://github.com/datarelay-labs/engineering-system/issues/317). Canonical repository: datarelay-labs/engineering-system. User directive: add the AI QA research conclusions to the roadmap and start implementation.

## Outcome

Detect actual novel defects in AI-developed software, independently validate final user outcomes, quantify injected-defect recall, false PASS, journey coverage and evidence reproducibility. Preserve existing full user-surface release gates, independent trusted evidence boundary and selected implementation runtime.

| Phase | Deliverable | Completion oracle | State |
| --- | --- | --- | --- |
| P0 | Portable blind QA policy + pinned evidence benchmark | Exact-head/digest gate, missing/false PASS/altered artifacts fail closed; 31 regression tests, CI | SOURCE + CI COMPLETE / DRAFT PR #318 |
| P1 | Model-led autonomous browser exploration and independent outcomes | Real Chromium, requirements-first pin, AI chooses each action from actual fresh observation; first trial found viewport QA-engine bug, second trial 10 model actions/11 observations with reversible controls, evidence auditor and bounded Playwright oracle | BOUNDED MODEL-IN-LOOP PASS / EXTERNAL PROVIDER AND FULL USER E2E NOT VERIFIED |
| P2 | Property, fuzz, metamorphic, mutation adapters | Historical immutable Parser Studio product source: pinned fast-check generative Vitest (3 original / 7 enhanced PASS); **standard StrykerJS** same 23 selected mutants: original 19/23, with independent holdout 23/23 (+4), both actual suites isolated and audited; broader unbiased source / API state / transport chaos still pending | REAL PINNED PRODUCT-SOURCE + STANDARD STRYKER PILOT PASS / BROADER QUALIFICATION PENDING |
| P3 | Owner-coordinated product pilots | Parser Studio real browser+backend+download; Link CLI; Control health; Grant read-only Chromium; Atlas read-only MCP; full E2E remains product-owned | PARSER PILOT PASS / 4 READ-ONLY TRIAGES, FORMAL USER E2E PENDING |
| P4 | Qualified adoption / optional QA SaaS bakeoff | Pinned tool + scoped observed 4/5 vs 5/5 strategy comparison; real QA.tech/TestSprite/Momentic invocation and independently verified cost later | OPEN-SOURCE SCOPED COMPARISON MEASURED / VENDOR RUNS NOT VERIFIED |

**New qualified P2 source experiment (2026-11-22):** [independent fast-check and standard StrykerJS A/B pilot](../evals/ai-qa/pilots/20261122-parser-studio-stryker-generative.md) on immutable Parser Studio source `567e0c2`: four seed-reproducible property cases (120 generations each), 3 original Vitest tests PASS, 7 with independent holdout PASS, **same 23 Stryker mutants 19 caught originally versus 23 with the holdout, four additional kills**. Reports and source are SHA-pinned. This is a selected historical product source and measured injected-defect detection, **not** proof of current product defect discovery, global accuracy, actual fresh ChatGPT UI flow or product release. The earlier falsely green 1,176-test Stryker comparison was explicitly excluded after its ignored testFiles setting was discovered and fixed.

## P0 deliverables

- standards/AI_QA.md and updated QUALITY/TESTING guidance; no change to the authorization or release gates.
- tools/ai_qa_benchmark.py read-only adversarial report comparator. Frozen plan SHA-256 and exact source HEAD must be externally supplied. Outputs never claim actual user gate PASS.
- tools/test_ai_qa_benchmark.py regression tests for the complete and intentionally faulty campaign, stale HEAD/plan tampering, unproven assertions, missing journeys, fake PASS, unknown bugs and artifact spoofing.
- evals/ai-qa sample plan, observation and sandbox-only artifacts demonstrating the file format.
- Add a cheap QA scenario and mapping in .engineering/tests.yaml; run the focused tests during native tools/validate.py.

**Latest measured P2/P3/P4 pilot evidence:** [real product-source mutation and cross-product read-only observations](../evals/ai-qa/pilots/20261024-product-source-mutation-and-cross-project.md). Parser Studio five selected source mutants: existing product suite killed 4/5; independent QA holdout added one detection (combined 5/5), exact product HEAD 567e0c255312028337898114cac452567bc585cc. This is not product-wide Mutation Score. Grant actual read-only Chromium login discovery PASS advisory; Link status permission, Control trusted-TLS backend, and Atlas exact-source-bound E2E remain unverified. External QA vendor results remain absent; no fabricated performance, pricing or provider calls.

**P1 adaptive model-in-loop live pilot (2026-10-24):** the selected ChatGPT actor drove a persistent real Chromium browser from current rendered observations rather than a prerecorded script. Run 1: five AI-selected actions discovered a QA explorer off-viewport-control bug. After viewport filtering, run 2: ten new AI-chosen actions, including selecting another event, scrolling below the long mapping table and returning safely, had zero browser errors and zero forbidden network requests. Both private screenshot/trace/decision ledgers passed the independent T0 evidence auditor. [Frozen evidence and limitations](../evals/ai-qa/pilots/20261024-autonomous-browser-exploration.md). The operator is CHATGPT_CHAT, not a separately attested second external model; the bridge requires live AI step decisions, and does not imply unattended LLM SaaS autonomy or release qualification.

## P1 deliverable sequence

1. Select one non-production browser product with stable requirements and existing Playwright CLI. Freeze externally observable missions before exposing implementation internals.
2. Run a real Chrome/Chromium session directly under the selected runtime; capture live trace/video/screenshot and final backend outcome separately.
3. Run a second independent QA exploration from requirements-only context. Midscene may be optional; it never writes or weakens the expected acceptance criteria.
4. Replay findings as deterministic Playwright regressions, compare against developer tests, record confirmed novel defects and costs.

## P2 deliverable sequence

1. Predeclare a sanitized hidden-fault corpus and clean controls, freeze SHA-256 externally.
2. Use Hypothesis/fast-check for type/round-trip/boundary/metamorphic invariants (not only one successful input).
3. Inject mutants with Stryker (JS/TS) or language-native equivalent; classify equivalent/uncovered mutations separately.
4. Where applicable, use Schemathesis for API stateful workflows and Toxiproxy for network partitions/delay/duplicates, only in isolated test environments.
5. Record fault recall, critical misses, false PASS, observed backend state and reproducible seed, then compare time/cost to existing tests.

## P3 product probes

- **Parser Studio:** broad real security log Golden Corpus, field precision/recall, byte-offset preservation, multi-event parsing and **first external ChatGPT UI response** import into real Parser Studio. A prompt API test is not a ChatGPT UI E2E substitute.
- **DataRelay Link:** real sending/receiving byte identity, retries, ordering, duplicate delivery, ACK and recovery.
- **DataRelay Control:** route CRUD, Run Now, UI/backend state consistency and interactive data-flow visualization.
- **DataRelay Grant:** two user sessions and mailboxes, approval denial/expiry, role policies, MFA, persisted audit.
- **DataRelay Atlas:** API/MCP stateful contracts, authorization and isolated recovery.

## Operational constraints and success metrics

Measure seeded defect recall by severity, false-PASS journey count (target zero), missing required checks, unmatched alerts requiring triage, independently confirmed actual false positives, reproducibility/flakiness and cost per confirmed new defect. Do not invent percentages or report PASS when user actions or external evidence have not been executed.

Do not mutate production, upload customer secrets/logs, create permission keys, force consumer adoption or claim release permission. Product owning Chats retain native PR/Git/CI and full user-gate responsibilities. Exact-HEAD CI and review must verify each completed code batch; P0 completion alone does not close this multi-phase roadmap.


## 2026-11-24 optional tool roadmap and cross-project outcome

These six optional tooling items were evaluated **after** independent P2
mutation qualification and PR #318 integration. All results are advisory;
none replace current owner-selected tool authority, native product CI, Full
User E2E, or actual release gates.

| Candidate | Disposition | Real, bounded evidence |
| --- | --- | --- |
| ES-TOOL-1 Chrome DevTools MCP | **IMPLEMENTED OPTIONAL** | PR #326 merged; real isolated Chrome/MCP source frames and local trace; no auth, production, or user-gate attestation |
| ES-TOOL-2 OpenCodeReview | **NOT_ADOPTED_WITH_EVIDENCE** | Pinned 1.14.1 Delegate/Preview against genuine DSP bug-fix PR; file grouping but no LLM-reviewed findings or independently verified defect gain |
| ES-TOOL-3 Modern Web Guidance | **IMPLEMENTED OPTIONAL** | PR #327 merged; 1.0.1 static guidance and pinned axe browser WCAG evidence; old Parser Studio snapshot 5 rules/23 nodes, no product fix claimed |
| ES-TOOL-4 reusable scoped skill | **IMPLEMENTED** | PR #325 merged; Worktree audit skill and existing repo_read profile preserve permissions and no side effects |
| ES-TOOL-5 OpenComputer-inspired runtime | **NOT_ADOPTED_WITH_EVIDENCE** | Actual two rootless namespace sessions separated files on Atlas, cancellation found an empty-dir lifecycle leak and dev-drcontrol denied userns; no general unattended runner |
| ES-TOOL-6 consumer adoption verification | **IMPLEMENTED ADVISORY** | `tools/ai_qa_consumer_adoption.py` authenticated 8-repo main census and exact-head CI observation with conservative unknown/failed coverage; `tools/test_ai_qa_consumer_adoption.py` guards false release/E2E claims |

Read real receipts and limits in
`evals/ai-qa/pilots/20261124-opencomputer-isolation.md` and
`evals/ai-qa/pilots/20261124-cross-project-adoption.md`.
The measured historical independent mutation gain remains **19/23 to 23/23**
on the same fault set (+17.39pp), not a blanket product accuracy claim.
Product-owned P3 complete Full User E2E, current Parser Studio accessibility
remediation, Link current-head CI failures and Control main adoption
reconciliation remain open and owned by their corresponding product teams.
The generic cross-project census is read-only and does not add a new release
gate or rewrite consumer branches.
