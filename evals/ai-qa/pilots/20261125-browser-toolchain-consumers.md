# Browser-toolchain consumer observation — 2026-11-25 KST

Canonical predecessor: PR #329, merged 429469d804120ceb78c50c2dadae75b09a04378a.
Pinned CLI 0.1.25 and MCP 0.0.86, explicit Chromium sandbox, isolated HOME,
stdio MCP, loopback fixtures, no customer data and no live service changes.

| Consumer | Exact source | Native preparation | CLI | MCP | Scope |
|---|---|---|---|---|---|
| Control | d909b32ae61360bf7a34490cfab0d2803cc2b193 | frontend build exit 0 | PASS | PASS | actual built sign-in surface, no backend |
| Grant | ce71ef3a8789f35f2c9a3edf927aa2317c5c87a1 (PR 102) | web build, frozen uv sync exit 0 | PASS | PASS | actual disposable API/SQLite/SMTP fixture sign-in surface only |
| Atlas | 076aec03465ceed22e375f2043dd1de43cf8794b | existing WebUiTests fixture | PASS | PASS | actual overview, search, source provenance and escaped title |
| Foundation | 2366b5c7d54446141673ef961b8bbd017c62f6b4 (PR 108) | control-clone build exit 0 | PASS | PASS | actual built sign-in surface at /settings, no backend |

Initial Atlas CLI and MCP assertions failed: the query term was in the
URL/input, not necessarily body.innerText because the displayed snippet
is truncated. Independent actual WSGI response inspection confirmed the
result, canonical provenance, source identifier and exact count. Corrected
the test to assert the URL query, README.md, escaped title, source relation,
readme@main, exact result count, repository/ref/path, and no script elements.
Both final drivers exited zero. This is test correction, not product defect.

Remote execution experienced repeated transport TIMEOUT; direct readbacks
subsequently verified build and final pilot exit codes. The first Grant
dependency setup failed because the unprivileged account could not traverse
the original uv binary's directory; a byte-identical disposable copy was
used for frozen uv sync, exit zero. No original source/worktree or host
permission policy was changed.

This is bounded actual browser-tool integration evidence (T0), not four
Full User E2E receipts. Existing product acceptance contracts remain
authoritative. Control authenticated route/receiver, Grant independent
mailbox/human approval, Foundation native RBAC/MFA/ACL/session and Atlas
MCP-primary acceptance still require their respective current product
gates. e2e-inc/e2e remains experimental; no new incremental defect-yield
claim. The four consumer workstreams were updated without a competing
product worktree or release authorization.
