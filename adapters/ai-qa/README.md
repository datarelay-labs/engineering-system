# Optional QA adapters — Parser Studio pilot

The core Engineering System is deliberately Python-only and does not install a browser, model or mutation service on adopters. These adapters are product-scoped examples, exercised on an authorized development instance.

## Real Chromium: input → analysis → field review → Ruby/CONF artifacts

Use a previously frozen mission.json with classification SYNTHETIC_SECURITY_EVENTS_NO_CUSTOMER_DATA, a development URL, and 1–30 synthetic records. Keep its SHA-256 outside developer-authored test output. No production host or third-party model is allowed by the browser adapter.

From this repository root, with the existing pinned Playwright installation of the product checkout:

    NODE_PATH=/path/to/parser-studio/node_modules node adapters/ai-qa/parser-studio-browser.cjs \
      --mission /secure/mission.json --frozen-sha256 FROZEN64 \
      --out /secure/qa-run-new-directory

This executes actual Chromium clicks on Auto Parser, enters original records, requests Multi-Pass, navigates Review/Map/Create Parser, downloads two DRAFT files, and captures capture.json, draft-run.json, screenshots and a Chromium trace. It reports CAPTURED_NOT_QUALIFIED, never a release PASS.

Independently compare the frozen raw values, exact UTF-8 field byte boundaries, actual backend response and SHA-256 of downloaded DRAFT files:

    python3 -B adapters/ai-qa/parser-studio-oracle.py \
      --mission /secure/mission.json \
      --capture /secure/qa-run-new-directory/capture.json \
      --draft /secure/qa-run-new-directory/draft-run.json \
      --evidence-root /secure/qa-run-new-directory \
      --expected-mission-sha256 FROZEN64

The current oracle targets the limited synthetic customized_demo_firewall DRAFT naming and port contract. Other missions need separate strict oracles rather than weakening this pilot. Every result remains T0_ADVISORY_ONLY; neither selected-runtime release acceptance nor Stellar native verification is conferred.

## Bounded property and metamorphic testing (P2)

Run only against the explicitly allowed development site. Input is safe random synthetic security JSON, not customer logs:

    NODE_PATH=/path/to/parser-studio/node_modules node \
      adapters/ai-qa/parser-studio-structural-fuzz.cjs \
      --seed 20261024 --cases 18 --out /secure/new-property-report.json

The Chromium same-origin Multi-Pass API requests analyze two events in both orders per case. The verifier checks full extracted values, UTF-8 spans, SHA-256 and the metamorphic relation that source record reversal preserves field extraction. Failed cases store reproducible seed/source. This is a custom seeded property/fuzz pilot, not external Hypothesis or Schemathesis.

## Evidence-level semantic mutation (P2)

After successful actual capture and independent oracle:

    python3 -B adapters/ai-qa/semantic-mutation-probe.py \
      --mission /secure/mission.json \
      --capture /secure/qa-run-new-directory/capture.json \
      --draft /secure/qa-run-new-directory/draft-run.json \
      --evidence-root /secure/qa-run-new-directory \
      --expected-mission-sha256 FROZEN64 \
      --out /secure/new-mutation-report.json

The tool changes copies of actual observed HTTP response data, re-hashes altered evidence and tests whether the independent oracle detects it. It does not mutate product source code or produce a Stryker code Mutation Score. Actual source mutation, API stateful fuzz and network fault injection remain product-native work.

## Actual disposable Python source mutation (P2)

The source-mutation-probe.py changes eight one-at-a-time predicates in **temporary copies** of the canonical ai_qa_benchmark.py source (not customer or product code) and runs the normal 31-case regression suite against each mutant. Baseline must be green. Mutants that survive are reported; invalid/timed-out mutants cannot be counted as killed. A score is bound to the source SHA-256 and stored as T0 advisory only.

    python3 -B adapters/ai-qa/source-mutation-probe.py --out /secure/new-source-mutation-report.json

This proves the QA benchmark's source regression sensitivity in a narrow subset. It is **not** a Parser Studio, DataRelay Link or other product source Mutation Score, and does not replace a later project-native Stryker/Hypothesis pilot.

## Frozen provider comparison (P4)

tools/ai_qa_bakeoff.py reads 2–12 tools' pinned QA observations and locally hashed artifacts against the SAME hidden holdout, exact HEAD and separately frozen campaign SHA-256. It reports missed critical seeded defects, false PASS journeys and claimed cost per seeded bug. Costs, actual provider invocations and actor identity remain unverified and T0_ADVISORY_ONLY. The resulting comparison order never grants procurement, release or merge authority.

    python3 -B tools/ai_qa_bakeoff.py --help

No QA.tech, TestSprite or Momentic paid account was connected or invoked in this pilot. Actual independent provider comparisons require independently collected real invocations and an authorized sanitized test environment.
