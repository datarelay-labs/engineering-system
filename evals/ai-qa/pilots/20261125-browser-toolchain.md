# Browser toolchain qualification — 2026-11-25 KST

Scope: synthetic loopback fixture, separate unprivileged development-server
temporary HOME, no product source/session/data/credentials or model provider
used. This is T0 advisory evidence, not a product Full User E2E receipt.
Node 24.21.0; CLI 0.1.25, MCP 0.0.86, e2e 0.24.0,
@e2e-dev/web 0.16.1; Playwright 1.66.0-alpha-2026-11-18.
The dependency install skipped lifecycle scripts.

| Path | Normal | Deliberate output corruption |
|---|---|---|
| Native Playwright | sentinel-20261125 | WRONG detected |
| Playwright CLI | actual input/click/evaluate, exact result | not separately tested |
| Playwright stdio MCP | actual snapshot/ref/type/click/snapshot, exact status | not separately tested |
| e2e deterministic locators | 1/1 PASS, runner 3.11 s | 1/1 assertion failure, exit 1, observed WRONG |

The first e2e test used a non-existent browser.getByRole method; corrected to
browser.locator and reran. The first browser download and worktree creation
calls timed out at transport level; later direct observations confirmed
completion. No model step/cache replay or actual product journey was tested.
The local environment was a separate OS user/HOME, not a proven network or
filesystem sandbox. No production or authentication policy was changed.

Disposition: optional CLI and stdio MCP adopted as development-only
toolchain; e2e remains experimental. The same seeded fault was detected
by native and e2e, so the observed incremental fault yield is zero. Require
a product-owned same-mission comparison before wider e2e adoption.
No new mandatory CI or release gate for consumers.

Local raw evidence SHA-256 (synthetic-only; logs retained in isolated lab):
- CLI: 75b6253bb92515aee46dca6d3a790852094a94cfcbfcaa1aecbf3cb24d592042
- MCP: 4e237daaaa5d5a7ec72dd3e9beed05b037bb8dd4015f89d5f61d5efa36fcee8a
- Native: d276dd9ec838f0cfc414a0ddeab9f08c41d0701a8b898cc54cd64ad7e85f9e93
- e2e normal: c8c3c506a1ebd4f79183ff46beee0a11b1c7bdcfcb6d25aba07d326824f3a2a5
- e2e fault: 5adef6e0f84071577bdb714c65eb7ed85a45b9318d14de511b961dca0f251f9a
- committed lock candidate: 5aa4914a97e6e81608e8b913c8b6a5bd279d16036bd95a63fe3c02a8ad5f7d3d

A separate path-filtered PR qualification workflow repeats the synthetic
comparison and archives its own evidence. Exact-HEAD CI outcome must be
read from GitHub, not inferred from these local results.
