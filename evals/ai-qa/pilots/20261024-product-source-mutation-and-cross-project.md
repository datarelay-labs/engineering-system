# AI QA portfolio — product source mutation and cross-product observations

Date: 2026-10-24. Workstream: Engineering System Issue #317 / Draft PR #318.
All results below are T0 advisory development QA evidence, not formal Full User E2E or release acceptance. No credentials, customer data, privilege elevations, production changes, destructive actions, product code commits or merges were performed.

## P2 — actual Parser Studio source mutation

- Exact product candidate: xdr-labs/parser-studio HEAD **567e0c255312028337898114cac452567bc585cc**, nonproduction dev integration 154+155+156, not GitHub main.
- Target: src/guided/normalizationValidation.ts; real TypeScript source mutations applied one at a time to disposable pinned Git archives, never product working tree or service.
- Product-native baseline: 3/3 original Vitest cases PASS. Added independent requirement holdout: conflicting hierarchical mapping targets custom.actor and custom.actor.id must both be flagged TARGET_COLLISION.
- Five syntax-valid targeted source mutants: original suite killed **4/5**; the independent new holdout killed **1 additional** (nested-target collision guard removed while identical-target check still works); combined suite killed **5/5**, with 0 invalid, 0 survived, 0 timeout.
- Bounded selected-mutant score: **80% original versus 100% with holdout**. This is **not** global Parser Studio mutation coverage and not evidence that unmodified source is defective.
- Result on dev-atlas: /home/aella/.local/state/engineering-ai-qa-pilot-20261024/parser-product-source-mutation-v2-20261024.json
- Report SHA-256: **2c55d2fa8cb55a69b0c106af60b9c82ddf56622fe90ccc5c2583449c490362f4**. Independent QA holdout SHA-256: **67b2d33e354d8ae2ee81f60cf98a85ed1021acb2829ea085a510ea02bd7384bc**.
- Adapter: adapters/ai-qa/parser-studio-product-mutation.py, product HEAD and source files externally pinned. Syntactically invalid mutants, timeouts and test infrastructure errors are excluded from kill scores. Real product repository never modified.

## P3 — independent read-only portfolio checks

| Product | Actual observation and exact binding | Qualification |
| --- | --- | --- |
| Parser Studio | Existing real Chromium pilot: 2 synthetic original security events, 14 exact source field/span comparisons, real backend and actual DRAFT Ruby+CONF downloads with trace; additional actual source mutation above | Narrow P1/P2 pilot PASS / not full product user release |
| DataRelay Link | dev-drlink candidate HEAD **03447356cfcaa453d64785107540f3cb6f9657d2**, real tools/drlink --help exit 0; prints action-first CLI. Read-only tools/drlink show status exit 1, requiring elevated install-state permissions. Error SHA-256 **0a0332ea22788286b987ac2903080d50c25bfb7dd5eac6991b17aec0b9284109**. No sudo attempted | Public CLI discovery PASS; selected unprivileged operational status BLOCKED. Full CLI/user E2E and unresolved performance gates remain with product owner |
| DataRelay Control | dev-drcontrol GET to **10.10.10.50:18080/health** responds 301 to HTTPS 18443. TLS verified GET fails curl 60 on self-signed trust, platform reverse proxy unhealthy. Loopback 127.0.0.1:18080 unreachable, HTTP root 200 only. No insecure TLS flag, login override, service reset or DB modification | Runtime / authenticated user outcome UNVERIFIED; product-owning QA must address current authenticated nonproduction environment |
| DataRelay Grant | dev-atlas private 127.0.0.1:19720, known source candidate HEAD **c1baed1b696a3645ca56ecee012597020f4f9586**. Real GET-only Chromium checked HTTP 200, signin form, username/password inputs, Sign In button and no self-registration, with 4 allowed network responses, 0 blocked writes. Frozen HTML SHA **adec06b44cd77e570bd6f4d73022a57cd2659f12da74341c5ece1439494a64b0**. Evidence report SHA **bea081172ec7f32268501b2fb0be2a5572301617bd0ecfa6aac0cb22c8642fa3**, screenshot + trace stored privately; current deployed runtime not independently source-bound by that HTML hash alone | PASS_READONLY_LOGIN_DISCOVERY_ONLY; two-person email/PIN/MFA decision E2E NOT VERIFIED |
| DataRelay Atlas | Connected read-only operations-readiness MCP returned registry/runbooks CONFIGURED, observability OBSERVED, but **source.head UNKNOWN** and no merge/deploy/release/pass authority | Read-only MCP observation qualified, exact-source-bound user workflow and full user E2E UNVERIFIED |

Results above identify what can be run with current authorization. A readable landing screen, CLI help, HTTP 200 or MCP observability receipt cannot satisfy a protected action's user mission. Only the product-owning Chats may converge product release gates and perform their authorized full user workflows.

## P4 — actual versus unverified comparison

The actual open-source testing-strategy comparison used the exact same five Parser Studio seeded code faults:

- Existing product-native regression: detected **4/5**.
- Existing suite with independent requirements holdout: detected **5/5**, one confirmed marginal improvement.

This is NOT a paid-platform or two-provider performance comparison. An existing frozen-manifest CLI (tools/ai_qa_bakeoff.py) is implemented and unit tested, but QA.tech / TestSprite / Momentic accounts were neither installed nor invoked. Provider completions **0**, provider prices and defect yield **NOT VERIFIED**. External SaaS evaluation requires actual accessible vendor accounts, sanitized held-out cases, and independently measured cost; no vendor figures are fabricated.

## Remaining work and scope

- P2 further unbiased/larger source Mutation, property-based library, API stateful and transport-chaos experiments require separate suitable nonproduction product fixtures; selected five mutants are a bounded proof, not total coverage.
- P3 full Link/Control/Grant/Atlas authenticated missions remain under their existing product owner roadmaps and safety/release constraints, not replaced by the advisory probes above.
- P4 meaningful commercial trial remains gated on an actual connected QA vendor and permitted test environment; comparison code alone does not provide results.
- Optional Midscene exploration should be adopted only after a measured incremental defect yield over existing deterministic Chromium tests.

Owner action for these non-destructive shared QA probes: none.
