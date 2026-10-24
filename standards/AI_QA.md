# Independent AI QA and E2E Quality Standard

## Goal and authority

AI-written code and AI-written tests can share incorrect assumptions. Complement existing L0-L8, Surface Reconciliation, Full User E2E, trusted T0–T5 verification and owner release acceptance with **requirements-first independent defect discovery**. None of those mandatory gates are replaced by a synthetic QA report.

The implementer remains the execution-profile-selected runtime. "Independent" refers to separately frozen expected outcomes, held-out test data and distinct observation context. It does not require a second provider or authorize an unselected runtime to impersonate the human-equivalent acting user. Actor names in JSON are declarations, **not trust anchors**.

## Blind QA contract

1. Freeze an externally observable user mission and expected outcome from approved requirements before reading implementation or developer assertions.
2. Keep seeded fault IDs, oracle mappings and holdout inputs away from the developer and the QA operator until evaluation. Freeze an external SHA-256 digest for the oracle.
3. Observe the **actual public action**, **final product outcome**, and **independent oracle** separately. UI toast/HTTP 200 is not a substitute for actual persisted or network state.
4. Bind every test to the exact source HEAD and, where applicable, deployed runtime/build fingerprint. Report unsupported and missing evidence explicitly as UNVERIFIED/BLOCKED.
5. Collect original reproduction input, random seed, sanitized artifact digest, independent failure status, and affected regression IDs. Complete safe independent journeys before bundled fixes.
6. AI-based locator healing may repair selectors/waits, but may not change acceptance criteria, delete failed checks or convert UNKNOWN to PASS.
7. Send only safe sanitized data to external AI providers. Fuzz, mutate and inject faults on authorized isolated test environments, not production.

## Cost-aware test sequence

- Fast deterministic affected validation first.
- Pinned, hidden-fault **blind benchmark** to measure defect recall and false PASS.
- Actual Chromium user journeys with Playwright CLI/Test Agents, optional visual exploration with Midscene; keep deterministic independent backend outcome checks.
- Property/metamorphic invariants with Hypothesis or fast-check, mutation scores with Stryker or a language-native equivalent, API stateful fuzz with Schemathesis, controlled transport faults with Toxiproxy where applicable.
- An isolated complete confirmation at the product release gate; keep native CI and selected-runtime Full User E2E as independent requirements.
- Evaluate optional commercial QA tools on the **same hidden seeded defects** after the above proves useful.

Do not require all these tools for every repository. Native product test selection controls the relevant subset and execution budget. No external tool installation is forced during canonical adoption.

## Quality accounting

- **Fault recall:** uniquely proven detected seeded faults / predeclared seeded faults, with CRITICAL/HIGH reported separately.
- **False-PASS journey:** PASS is declared despite a predeclared known defect in its coverage; never accept.
- **Unmatched finding:** reported issue with no match in the frozen fault manifest; requires independent triage. It is **not automatically a false positive**, since real unseeded bugs can exist.
- **Verified mission:** public action, observed final outcome and independent oracle all have actual, independently validated evidence. A report with only counts is UNVERIFIED.
- **Flakiness:** outcomes vary under the same input/seed without a relevant product change; investigation required, not rerun-until-green.
- **Economics:** confirmed important new defects per cost/runtime, reproducibility, and false-positive rate based on independently classified clean controls (not merely unmatched IDs).

## P0 benchmark tool

The canonical tools/ai_qa_benchmark.py is a **read-only, bounded, advisory-only T0 evaluator**. It never executes browser tests, issues permissions, verifies the asserted actor identity, launches faults, validates raw reproduction semantics, or authorizes user acceptance/release. Its BENCHMARK_COMPLETE output only means pinned benchmark evidence *accounting* is internally complete.

Arguments: --plan (frozen JSON manifest), --report (independent QA observation JSON), --artifacts-root (hashed local evidence files), --expected-head (40 lowercase hex) and --expected-plan-sha256 (64 lowercase hex frozen externally).

The plan declares repository, HEAD, revision, plan ID, journeys with required public_action/final_outcome/independent_oracle checks and seeded faults with severity and journey. The report declares matching identity, distinct developer/QA actor identities and context IDs, per-journey result, checks and finding IDs matched to seeded faults. Each decisive check and finding references a real bounded local artifact by relative path and SHA-256; traversal, symlinks and digest changes are forbidden.

Exit codes: 0=BENCHMARK_COMPLETE on pinned campaign; 2=QUALITY_FAIL or INCOMPLETE; 3=BLOCK for malformed, conflicting or tampered input. Always inspect the advisory-only authority field. This is **not a trusted T1–T5 receipt, product QA PASS or Full User E2E PASS**.

See docs/AI_QA_ROADMAP.md and evals/ai-qa for sample inputs. Product Chat owns actual implementation and live browser user evidence.
