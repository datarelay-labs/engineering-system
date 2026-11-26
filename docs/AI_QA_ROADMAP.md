# Independent AI QA — roadmap and evidence index

The live roadmap and remaining actions belong to [Issue #317](https://github.com/datarelay-labs/engineering-system/issues/317). This page is a reference index, not a second Work Packet or a current product-release status ledger. The normative contract is [AI_QA](../standards/AI_QA.md); [USER_ACCEPTANCE](../standards/USER_ACCEPTANCE.md) defines mandatory product user gates.

## Available capabilities and measured evidence

| Capability | Evidence and limits |
| --- | --- |
| Blind fault benchmark | [PR #318](https://github.com/datarelay-labs/engineering-system/pull/318) merged; [synthetic example](../evals/ai-qa/README.md), advisory T0 accounting only |
| Model-led browser exploration | [Historical two-run Chromium pilot](../evals/ai-qa/pilots/20261024-autonomous-browser-exploration.md); no unattended runtime or formal user-gate attestation |
| Property and source mutation | [Pinned fast-check and Stryker A/B pilot](../evals/ai-qa/pilots/20261122-parser-studio-stryker-generative.md): same 23 selected mutants, original 19 detected, independent holdout 23 detected; not a product-wide score |
| Product-scoped experiments | [Historical five-mutant and read-only product probes](../evals/ai-qa/pilots/20261024-product-source-mutation-and-cross-project.md); not evidence of current product state |
| Browser diagnostics and tooling | [Chrome DevTools](../adapters/chrome-devtools/README.md), [Playwright CLI/MCP](../adapters/ai-qa/browser-toolchain/README.md), [web guidance](../adapters/web-guidance/README.md); optional isolated development tools |
| Cross-project adoption census | [Historical read-only audit](../evals/ai-qa/pilots/20261124-cross-project-adoption.md); CI and declared policy are not live user acceptance |
| Rejected or deferred experiments | OpenCodeReview: no independently measured defect gain; [OpenComputer isolation](../evals/ai-qa/pilots/20261124-opencomputer-isolation.md): bounded experiment only, no unattended runner adoption. Commercial QA vendors remain unqualified. |

## Follow-through

Product-owning Chats select the relevant existing tests, public-surface contracts,
independent oracle, and isolated fixture. Do not add duplicate mandatory gates
or take over delegated product work. For future experiments, freeze the fault
corpus and clean controls, compare the same test set, retain exact-source evidence,
and report fault recall, false PASS, confirmed novel defects, reproducibility,
and measured cost. Unsupported outcomes and missing evidence remain UNVERIFIED.
Real Chat effectiveness is tracked separately in [Issue #309](https://github.com/datarelay-labs/engineering-system/issues/309) and its [canary contract](../evals/behavior/REAL_CHAT_CANARY.md).
