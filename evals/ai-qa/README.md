# Independent AI QA example

Run:

    python3 -B evals/ai-qa/demo.py

The demonstration creates a **synthetic** frozen plan and a matching QA report inside a temporary directory, with hashed dummy artifacts and a **fake HEAD** (40 × a). It invokes the real evaluation CLI. It does **not** drive Playwright, ChatGPT UI, a backend or a trusted runtime and must never count as real Full User E2E, a verified provider comparison, release or project acceptance.

## Integrate an actual product pilot

1. Independently define and **freeze** expected journeys against the product requirements, using the JSON plan fields from demo.py. Keep seeded defects and plan hash hidden from the QA actor.
2. Record the exact product repository/HEAD and the actual deployed version. Safely inject known defects only in an isolated test candidate.
3. Run real user missions via the selected runtime on the product's supported public interface, including actual Chromium/Playwright if it is a browser product. Independently read final backend/network outcomes and compare them with the external oracle. Capture separate evidence artifacts.
4. Normalize observations with actor/context declarations, journey/check statuses, seeded-defect findings and SHA-256 of each captured file. Do not fabricate artifacts or use synthetic fixtures as real outcomes.
5. Execute tools/ai_qa_benchmark.py with externally frozen --expected-head and --expected-plan-sha256. Treat BENCHMARK_COMPLETE as **accounting only**: actual verified user action, release-grade trust and native CI still require their respective gates.
6. Keep missed critical/high defects, false PASS, unmatched findings, independent clean controls, flaky repeatability, and confirmed new bug reproduction separate in the report.

Never upload customer raw security logs, secrets or identifying traffic to hosted models without proper authorization and sanitization. See standards/AI_QA.md.
