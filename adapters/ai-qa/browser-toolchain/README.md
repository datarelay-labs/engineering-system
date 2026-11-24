# Optional browser toolchain (development only)

Pinned CLI and stdio MCP for AI-selected browser exploration; e2e-inc/e2e is
an experimental comparator, not an adopted release gate. Existing product
Playwright tests and independent outcome oracles remain authoritative.
The packages currently resolve to a Playwright alpha build; do not upgrade
product runtime or mandatory CI dependencies automatically.

Use a disposable, unprivileged, network-restricted development sandbox with
no mounted credentials, no customer data, a fresh HOME and browser cache.
Copy this directory into it. Install with Node 24.8+ and Python 3:

    npm ci --ignore-scripts --no-audit --no-fund
    node_modules/.bin/playwright install chromium
    E2E_TELEMETRY_DISABLED=1 python3 cli-smoke.py
    E2E_TELEMETRY_DISABLED=1 python3 mcp-smoke.py
    E2E_TELEMETRY_DISABLED=1 CI=1 node_modules/.bin/e2e run --strict-cache --workers 1 --assertion-timeout 3000

The deliberately faulty fixture must return nonzero and report
ASSERTION_FAILED with observed WRONG:

    E2E_TELEMETRY_DISABLED=1 CI=1 POC_FAULT=1 node_modules/.bin/e2e run --strict-cache --workers 1 --assertion-timeout 3000

For authorized interactive exploratory work, use the pinned local
node_modules/.bin/playwright-cli executable with a fresh named nonpersistent
session, or node_modules/.bin/playwright-mcp --headless --isolated --sandbox over stdio.
Do not expose an MCP port, attach to an existing personal profile, grant
unrestricted file access, or pass real session tokens. Origin allowlists are
not security boundaries. Apply the existing product mission/origin/write
restrictions and independent outcome verification; use an external
network boundary when evaluating nonlocal apps.

The e2e smoke uses deterministic locators only (no agent calls, credentials,
cache replay, or external models). Strict-cache alone is not a model-call
prohibition for missing recordings. No unreviewed agentic test may become a
release gate. The fixture is synthetic; passing is not product acceptance.

Run `node native-smoke.cjs` against the same normal and fault fixture for the
native Playwright comparison. A seeded fault detected by both approaches is
not evidence of incremental defect yield.

### Chromium sandbox portability

Keep the native, CLI and MCP smoke browser sandbox explicitly enabled.
The first Ubuntu 24.04 hosted-runner qualification failed with Chromium
`No usable sandbox!` in MCP. The qualification workflow selects Ubuntu
22.04 to test on a compatible hosted image without changing kernel,
AppArmor, or browser security settings. Do not treat a missing usable
sandbox as a test PASS or silently fall back to an unsandboxed launch.
The e2e comparator is limited to a synthetic loopback fixture; its engine
has its own browser launch policy and is not approved for live products
by this smoke.

### Optional product observation probes

In a private disposable HOME with the pinned dependencies and Chromium
installed as above, run the existing product-owned fixture on a fresh
loopback port. Copy `consumer-driver.py` into that fixture's private
working directory and run:

    python3 consumer-driver.py atlas cli http://127.0.0.1:PORT
    python3 consumer-driver.py atlas mcp http://127.0.0.1:PORT

Replace `atlas` with `control`, `grant`, or `foundation` as applicable.
The three latter probes observe the actual sign-in surface only
(Foundation at /settings); they do not log in or change credentials.
Atlas uses the actual WebUiTests disposable source/projection fixture,
checks cross-project search, exact provenance and one result, and checks
that the fixture's escaped title remains text rather than executable DOM.

The driver expects the shared pinned node_modules under its private HOME.
Only use a fresh test fixture, not a live or existing operator service;
the loopback origin check and origin allowlist are not a network security
boundary. The product owner must bind the fixture/build to an exact Git
HEAD and preserve its own independent acceptance oracles. Do not print
credentials, attach personal browser profiles, or claim any public smoke
as a formal BFS, FUE, deployment or release receipt.
