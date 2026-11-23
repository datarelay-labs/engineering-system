# Optional Chrome DevTools MCP read-only QA diagnostic

This is an explicit, disposable nonproduction diagnostic adapter. It does not
replace the existing Playwright explorer, alter its owner-selected model
actions, grant any MCP/OS/GitHub permission, run automatically or attest Full
User E2E. It adds source file/line fault localization and an optional
performance trace when the basic browser observer has only message text and
HTTP status codes.

## Verified runtime and supply chain

Use a separately created, isolated development directory and install the
official ChromeDevTools/chrome-devtools-mcp npm package at exactly version
1.12.0 with package-lock integrity checking and npm scripts disabled.
Do not use latest or a global user browser instance. The CLI checks package
name/version locally but does NOT cryptographically attest the executable;
use the independently pinned npm lock and an approved local environment.
The original proof used npm SHA512 integrity:
sha512-lAGpH0zXT1ZdaPefP1KK/MvxwihOlu7VGQ7p4wbhD9OhN17vUC4KWk/ImUYR9DE4s+7r4r0ySV4xr2OkTa7ZMA==

Runtime requires Node 20.19+, 22.12+ or >=23 and Chrome 149+ for enforced
URL-pattern filtering. The verified test used Node 24.21.0 and Chrome for
Testing 156.0.8078.4.

## Safe invocation

Start a standalone, disposable HTTP fixture on loopback port >=1024.
Never run against Production or a private/authenticated application.
Invoke the adapter manually after separately installing the pinned server:

    node adapters/chrome-devtools/readonly-probe.cjs \
      --target http://127.0.0.1:44321/ \
      --mcp-binary /absolute/isolated/node_modules/.bin/chrome-devtools-mcp \
      --chrome-binary /absolute/chrome-for-testing/chrome \
      --out /tmp/es-devtools-example20261123 --trace

The output directory must not exist and must be a direct child of the OS
temporary directory with name es-devtools-*. A successful call writes
diagnostics.json (0600) with ONLY Console message hashes and sanitized
file/line/column locations, HTTP status counts and URL-path hashes, and an
optional local raw trace (0600) plus digest. It never writes Console bodies,
network response bodies, user data, headers, query parameters or raw URLs to
the JSON report. The raw optional trace is sensitive debug evidence and must
not be uploaded to Issues, external SaaS, or a repository; clear the
one-time nonproduction artifact per local retention policy.

The subprocess launches isolated headless Chrome with fresh browser storage,
prevents new file navigations, disables code-evaluation entry points, input
tools, network mocks, extensions, third-party tools and CrUX requests,
disables MCP usage statistics and config discovery, and restricts the page
origin to the explicitly selected localhost port. This is NOT an OS firewall,
not cryptographic runtime attestation, and not proof of all process egress.
Run only in a trusted disposable development environment.

A diagnostic receipt has authority T0_ADVISORY_ONLY,
full_user_e2e NOT_ESTABLISHED, and release_authority NO.
Test the independent pure redaction contract with:

    node --test adapters/chrome-devtools/test-readonly-contract.cjs

## Recorded proof

See evals/browser-debug/20261123-chrome-devtools-readonly-pilot.md for an
independent actual local Chromium/MCP run and baseline comparison. The normal
CI contract suite uses no npm install, no browser, no network and no secrets.
