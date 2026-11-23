# ES-TOOL-1: Chrome DevTools MCP incremental diagnostic qualification

**Date:** 2026-11-23 KST
**Target:** Isolated localhost HTTP fixture, not a real product tenant.
**Decision:** Adopt as an *optional* developer-only Console/Network/performance
diagnostic, not as an agent browser replacement or source of user-gate truth.

## Controlled before/after observation

- Existing AI QA Playwright explorer uses Console error message truncation
  (200 chars), pageerror text, HTTP response method/path/status, and
  screenshots/trace archives. It does not persist Console JavaScript
  source-file line/column frames or structured performance insights.
- We constructed a real localhost browser fixture containing one
  deliberate JavaScript error, one console error, an HTTP 503 and a
  170 ms synthetic busy loop. No user login, API token, customer
  data or real product modification.
- **Playwright baseline on the identical fixture and Chrome executable:**
  4 browser errors, HTTP 503 count 1, source-frame fields 0,
  structured performance insight fields 0.
- **Official Chrome DevTools MCP v1.12.0 actual execution:** MCP protocol
  2025-06-18, Chrome for Testing 156.0.8078.4, Node 24.21.0.
  Console included JavaScript file/line/column positions:
  boom.js:1:9 and boom.js:2:7. HTTP 503 identified in the Network
  request list. Performance trace saved at 118,149 bytes with
  structured insight text. Additional localized frames and performance
  analysis were truly present; no confirmed unmodified-product bug
  or production remediation is claimed.
- Security flags: usage-statistics OFF, CrUX OFF, isolated headless
  Chrome, JavaScript evaluation OFF, file navigation OFF, page URL
  pattern restricted to exact localhost origin, no attached user session.

## Immutable local proof (owned DEV DR Atlas disposable test sandbox)

- Package version: chrome-devtools-mcp@1.12.0.
- npm lock SHA-256:
  1487029329423c526e090cfddcc6a77a1c2cb1f0134b8dbb0eaf3baf12e52e90
- Repro script SHA-256:
  50e53f803b582f575b0d115b230c588cd97614677fd1cedf803eec02d8e26ddd
- Sanitized result JSON SHA-256:
  a590e3a9cda0e68755ce8dd660ccd92c8796c29a48f98425458aa2eaff3c0c85
- Local private performance trace SHA-256:
  5839fa8040f0f4c84bf1da6eedba98ecae3ad5aad476f5515dfc17d77cb7d51b
- Result: MCP_OBSERVED, source-frame unique=true,
  performance-trace unique=true.

## Implementation and trust boundary

The opt-in adapter accepts only nonprivileged loopback fixture URL root
with no username, password, query or fragment. Chrome 149+ and pinned
local package metadata are checked before the server is launched. All
agent-visible output is privacy minimized; Console payload and URL
paths are hashed, while source basenames and line numbers and HTTP
status codes remain for debugging. Sensitive optional trace remains
a private local file. No default browser or remote user session
is reused, and the adapter exposes NO new authority to calling agents.

Node built-in contract tests run in native Engineering System CI
without downloading or executing the third-party server. A future
product-owning Full User E2E requires the product's actual real-user
scenario contract, source-bound run and independent auditor; this
optional local QA diagnostic is never an acceptance gate by itself.

**Residual limitations:** Chrome DevTools MCP dependencies must be
installed from the pinned verified package in a disposable trusted
development environment. Browser URL restriction is not a full OS
network sandbox. Source map frames may differ by product/browser;
no unsupervised separate AI browser agent is introduced. The
model-in-the-loop QA explorer in Draft PR #318 remains separately
owned and unmerged; this extension does not edit that worker's
dirty Worktree.
