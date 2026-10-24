# AI QA & E2E Quality Factory roadmap

Workstream: [Engineering System Issue #317](https://github.com/datarelay-labs/engineering-system/issues/317). Canonical repository: datarelay-labs/engineering-system. User directive: add the AI QA research conclusions to the roadmap and start implementation.

## Outcome

Detect actual novel defects in AI-developed software, independently validate final user outcomes, quantify injected-defect recall, false PASS, journey coverage and evidence reproducibility. Preserve existing full user-surface release gates, independent trusted evidence boundary and selected implementation runtime.

| Phase | Deliverable | Completion oracle | State |
| --- | --- | --- | --- |
| P0 | Portable blind QA policy + pinned evidence benchmark | Exact-head/digest gate, missing/false PASS/altered artifacts fail closed; focused adversarial tests + native validation | IN PROGRESS |
| P1 | Actual browser journey + independent backend oracle | Live Chromium/Playwright trace, user action, backend readback, isolated QA context; optional Midscene exploration | PLANNED |
| P2 | Property, fuzz, metamorphic, mutation adapters | One product-native pilot for each relevant class; frozen seed, surviving mutants, detected faults and false-pass measurement | PLANNED |
| P3 | Owner-coordinated product pilots | Parser Studio, Link, Control, Grant, Atlas independent defect discovery and verified native E2E results | PLANNED |
| P4 | Qualified adoption / optional QA SaaS bakeoff | Compare real confirmed defect yield, false alarms, replay quality and cost on identical hidden-fault corpus | PLANNED |

## P0 deliverables

- standards/AI_QA.md and updated QUALITY/TESTING guidance; no change to the authorization or release gates.
- tools/ai_qa_benchmark.py read-only adversarial report comparator. Frozen plan SHA-256 and exact source HEAD must be externally supplied. Outputs never claim actual user gate PASS.
- tools/test_ai_qa_benchmark.py regression tests for the complete and intentionally faulty campaign, stale HEAD/plan tampering, unproven assertions, missing journeys, fake PASS, unknown bugs and artifact spoofing.
- evals/ai-qa sample plan, observation and sandbox-only artifacts demonstrating the file format.
- Add a cheap QA scenario and mapping in .engineering/tests.yaml; run the focused tests during native tools/validate.py.

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
