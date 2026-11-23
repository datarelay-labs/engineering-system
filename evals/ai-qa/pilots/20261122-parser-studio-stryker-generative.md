# Independent Parser Studio property + standard StrykerJS A/B pilot

- Date: 2026-11-22 KST.
- Workstream: [Engineering System #317](https://github.com/datarelay-labs/engineering-system/issues/317), Draft PR #318.
- Status: **BOUNDED P2 SOURCE-LEVEL EXPERIMENT PASS**, not a product release or user E2E gate.
- Actual run: [Isolated P2 #44920637836](https://github.com/datarelay-labs/engineering-system/actions/runs/44920637836) COMPLETED/SUCCESS.
- Scope: Public, immutable, **historical** nonproduction Parser Studio source `xdr-labs/parser-studio@567e0c255312028337898114cac452567bc585cc`. This is **not** proof about November 2026 Parser Studio current HEAD or any running deployment.
- Mutated source: `src/guided/normalizationValidation.ts`, SHA-256 `2bd2533b649821c16ae2d68c47411fc075ef2db499bf4292a82273d7e0b9e93e`.
- All package installations, test injections and mutants were confined to ephemeral GitHub Actions checkouts; no product source branch, dirty worktree, service, credentials, customer data or deployment was modified.

## Independent property challenge

`adapters/ai-qa/parser-studio-generative-holdout.test.ts` uses pinned `fast-check@4.3.0`, deterministic seed **20261122**, four generative properties with **120 runs per property**, and the library's built-in reproducible shrinking on failure. It tests nested target collisions in both directions, dot-boundary prefix noncollisions, missing-source honesty, mapping-order invariance, and original Unicode/source value preservation.

The real product-native Vitest baselines both passed:

- Original developer suite: **3 tests PASS**.
- Original suite with independent holdout: **7 tests PASS**.

These are real Vitest executions on the pinned product code, not Python simulations.

## Standard StrykerJS A/B measured results

- Actual installed versions: `@stryker-mutator/core@9.3.0` and `@stryker-mutator/vitest-runner@9.3.0`.
- Two independent StrykerJS runs used the same pinned source, selected mutation ranges `26-28` and `35-37`, and identical generated mutant definitions.
- Crucially, the original Stryker dry run executed **exactly 3 tests**, and the enhanced dry run executed **exactly 7 tests**, confirmed in GitHub logs. The comparison script checks mutant identity, head/source SHA and disallows false kills or synthetic user E2E authority.

| Measured category | Existing product tests | Existing + independent holdout |
| --- | ---: | ---: |
| Attempted / qualified TS mutants | 23 / 23 | 23 / 23 |
| KILLED | **19** | **23** |
| SURVIVED | **4** | **0** |
| Unqualified / timeout / compiler error | **0** | **0** |
| Scoped mutation score | **82.61%** | **100.00%** |
| Confirmed marginal kill improvement | — | **+4 mutants (+17.39 percentage points)** |

This is a **selected, bounded StrykerJS experiment**, not whole-product mutation coverage, field-mapping accuracy or confirmed product defect discovery.

### Frozen provenance

- GitHub Actions: `44920637836`, actual 3-test/7-test Stryker dry-run outcomes and scorer output in job log.
- Original mutation report SHA-256: `0eeb8ff7f64415fe44f6dacfcc4595317f4d6241e0a073a812cc72e2493b894c`.
- Enhanced mutation report SHA-256: `d3ad29cfee82ebafc47bc62fde2207e65cbb1e11d8a068bea40699c42bfb074a`.
- Comparator score report SHA-256: `701a543ba486c116034a4d98d4686e28b518d6e87cbc1626a6e506e666ce5232`.
- Artifact retention and top-level dependency snapshot have been added to the workflow for subsequent exact-head runs. Do not infer an artifact upload from the older run above.

### Invalidated earlier attempt (excluded from all results)

[Run #44920317109](https://github.com/datarelay-labs/engineering-system/actions/runs/44920317109) used an unsupported Stryker `testFiles` setting. Both Stryker runs silently executed **all 1,176 product tests**, returning an apparent 23/23 versus 23/23 with zero marginal gain. Despite a green workflow, it did **not** isolate an independent holdout and is disqualified as A/B evidence. An intermediate runner configuration had incorrect newline escaping and failed closed; it also contributes no result. The accepted experiment above uses separate Vitest `configFile` include filters and asserts exact dry-run test counts.

## Limitations and follow-up

This result qualifies only the specifically frozen 23-source-mutant challenge and demonstrates extra detection of **deliberately injected faults**. It found no verified defect in the unmodified Parser Studio. Product-owner current-HEAD accuracy, real user-assisted parsing, source-to-deployed-artifact binding, native Fluentd/sensor, Surface Reconciliation, and Full User E2E remain separately NOT VERIFIED.

DRLink MCP lists DEV DR Atlas as connected but host-info/command execution on the selected endpoint returned `DENY: implicit DENY` in the latest attempt. No policy bypass or alternative identity/host was attempted. Re-qualifying against a fresh product HEAD via dev-atlas requires authorized access and current owner worktree/scope reconciliation; the historical immutable-source GitHub Actions challenge does not stand in for that.
