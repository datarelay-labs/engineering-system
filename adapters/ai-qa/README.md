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

## Model-selected independent browser exploration (P1)

The provider-neutral autonomous-browser-explorer.cjs runs a **persistent real Chromium** browser but intentionally does not embed an external LLM API. The selected CHATGPT_CHAT operator must read each actual UI/HTTP/screenshot observation, choose the next button/input/scroll/press/select/back action, and provide a reason. An action must reference the latest fresh observation ID and currently visible element; replaying stale selectors, attempting external origins, credentials or unapproved writes is denied. Offscreen controls appear only after a real scroll and new observation, including long mapping forms.

A synthetic-only mission must be frozen and given with its exact SHA-256 before browsing. The current public development site allows only same-origin GET/HEAD/OPTIONS and a predeclared safe preview-batch POST, no state-changing parser generation. Evidence stays in a new private directory, with JSONL action/observation journal, screenshot digests, real Playwright trace and advisory result. No commercial LLM/QA account is required or invoked by the adapter.

    PLAYWRIGHT_MODULE_ROOT=/path/to/existing/playwright/node_modules node \
      adapters/ai-qa/autonomous-browser-explorer.cjs \
      --mission /private/frozen-synthetic-mission.json \
      --expected-plan-sha256 INDEPENDENT_FROZEN_SHA256 \
      --expected-html-sha256 FROZEN_REAL_RUNTIME_HTML_SHA256 \
      --out /private/new-qa-run

The process accepts newline-delimited model-chosen JSON commands on standard input, such as action kind click with observation_id, ref and a decision reason; it prints AIQA_OBSERVATION for the next selected step. Allowed final disposition is NO_CONFIRMED_FINDING, POTENTIAL_FINDING, BLOCKED or UNVERIFIED, **never release PASS**. See the complete actual [two-run pilot](../../evals/ai-qa/pilots/20261024-autonomous-browser-exploration.md).

Independently check causal action order, pinned runtime/mission, screenshot hashes and trace (advisory, not external identity attestation):

    python3 -B tools/ai_qa_explorer_audit.py \
      --mission /private/frozen-synthetic-mission.json \
      --expected-mission-sha256 INDEPENDENT_FROZEN_SHA256 \
      --expected-html-sha256 FROZEN_REAL_RUNTIME_HTML_SHA256 \
      --run-dir /private/new-qa-run

This live model-in-the-loop mode is **not** unattended provider autonomy; there is no verified separate LLM invocation, and a scripted replay or a user-agent declaration cannot stand in for selected-runtime Full User E2E. Deterministic CI uses Node built-in contract tests and Python stdlib evidence tests; it never requests a real public service or installs model credentials.

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

## Actual Parser Studio product-source mutation (P2)

A separate product-scoped adapter now archives the immutable exact Parser Studio HEAD into a disposable temporary sandbox, links the *already installed* pinned Vitest dependencies, executes the unchanged product-native normalization regression against five syntax-checked TypeScript source mutants, then adds an independent requirements-derived holdout for nested mapping destination collisions. The original checkout, its node_modules and its deployed service are not modified.

Command (use the qualified nonproduction 154+155+156 source, and a NEW private output path):

    python3 -B adapters/ai-qa/parser-studio-product-mutation.py \
      --product-root /home/aella/parser-studio-dev-integration-154155156-20261024 \
      --expected-head 567e0c255312028337898114cac452567bc585cc \
      --out /private/new-product-source-mutation.json

The initial five-mutant pilot found original product tests kill 4/5 and independently added holdout kills 1 more. These are intentionally selected mutants, **not** general mutation coverage or E2E release quality. Test cases never access actual Stellar target, production accounts or host credentials. See the 20261024 cross-product pilot report.

## DataRelay Grant read-only Chromium discovery (P3)

The separate grant-login-readonly.cjs adapter navigates only an operator-selected **localhost** development Grant entrypoint in real Chromium. Network requests are restricted to the same origin and GET/HEAD/OPTIONS; any POST/external request is blocked, no credentials entered, and no approval action exercised. It requires independently pinned raw HTML SHA-256 and a NEW private evidence directory; it captures screenshot, trace and a limited result receipt. This proves only login discoverability, never two-user email/MFA or Full User E2E.

    PLAYWRIGHT_MODULE_ROOT=/path/to/existing/pinned/playwright/node_modules \
      node adapters/ai-qa/grant-login-readonly.cjs \
      --url http://127.0.0.1:19720/ \
      --expected-html-sha256 PINNED_PUBLIC_HTML_SHA256 \
      --out /private/new-grant-readonly-evidence

## Frozen provider comparison (P4)

tools/ai_qa_bakeoff.py reads 2–12 tools' pinned QA observations and locally hashed artifacts against the SAME hidden holdout, exact HEAD and separately frozen campaign SHA-256. It reports missed critical seeded defects, false PASS journeys and claimed cost per seeded bug. Costs, actual provider invocations and actor identity remain unverified and T0_ADVISORY_ONLY. The resulting comparison order never grants procurement, release or merge authority.

    python3 -B tools/ai_qa_bakeoff.py --help

No QA.tech, TestSprite or Momentic paid account was connected or invoked in this pilot. Actual independent provider comparisons require independently collected real invocations and an authorized sanitized test environment.
