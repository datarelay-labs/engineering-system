# Engineering System #309 — actual Chat reliability canary

This is an **evidence-only** companion to the existing behavior-eval tool, not
a new automation framework. The canonical Engineering System change is
[merged PR #310](https://github.com/datarelay-labs/engineering-system/pull/310),
commit `3d719ec52254cf7d53735b285b6f5bcb34711601`.

Do **not** interrupt or reorder a product's ACTIVE Work Packet to run this
pilot. Each product Chat may record qualifying observations **after** its
product-owned, compatible canonical adoption. A verified outcome requires an
actual independent fresh-Chat observation and authenticated supporting evidence;
a unit test, CLI fixture or AI self-report is never that evidence.

## Fixed 30-run target

- Repositories: Product Foundation, DR Control and Grant.
- Scenarios per product: `P0_FIRST_ACTION`, `CONTINUE_UNFINISHED`,
  `BLOCKED_P0_YIELD`, `STALE_HEAD_RECONCILE`, `FALSE_COMPLETE_REJECT`.
- Two separate fresh-Chat runs per scenario, `ATTEMPT=1|2`; total 30.

The existing product issue comments under Foundation #105, Control #354
and Grant #37 define the product-owned collection workflow. Only add evidence
from naturally performed real scenarios; never simulate a Chat or claim a
model's first action from generated data.

## Bounded record contract

The JSON `records` array is initially empty. Each qualifying observation has
exactly these fields; free-form transcripts, raw commands, API credentials and
personal data are prohibited:

```json
{
  "PRODUCT": "foundation",
  "CASE": "P0_FIRST_ACTION",
  "ATTEMPT": 1,
  "RUN_ID": "foundation-p0-first-action-1",
  "SESSION_DIGEST": null,
  "INITIAL_PACKET_ISSUE": 99,
  "INTENT_REVISION": 1,
  "BRANCH": "example/isolation",
  "HEAD": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "EXPECTED_FIRST_ACTION": "first-unfinished-task-id",
  "OBSERVED_FIRST_TOOL_AND_TARGET": "mcp__GitHub__fetch_issue:datarelay-labs/datarelay-product-foundation#99",
  "SUPPORTING_GIT_OR_TOOL_EVIDENCE_URL": "https://github.com/datarelay-labs/datarelay-product-foundation/issues/99#issuecomment-123",
  "RESULT": "UNKNOWN",
  "USER_REDIRECTIONS_COUNT": null
}
```

This example is **not real evidence**. `SESSION_DIGEST` may remain `null`
when the host does not expose a reliable session identity. Do **not** invent
conversation IDs or hashes. If a host-authorized stable session identifier
really exists, store only a non-reversible SHA-256 digest, never the raw ID.
Use distinct authenticated GitHub evidence comments for each run. `RESULT` is the *reported* observed result
(PASS/FAIL/BLOCK/UNKNOWN), not an independent auditor's verdict.

Run from the Engineering System repository:

```sh
python3 -B tools/behavior_eval.py real-chat-report \
  --evidence evals/behavior/real-chat-canary-observations.json
```

The deterministic reporter checks schema, slot completeness, duplicates,
non-secret identifiers and GitHub evidence-URL shape. It does **not** fetch
Chat sessions, authenticate GitHub evidence or independently prove user
behavior. Its result always reports `success=NOT_VERIFIED`, even when 30/30
reported outcomes claim PASS. The Engineering System Chat audits each cited
authentic artifact and the actual first-action behavior separately.

Do not claim improvement versus an unmeasured historical baseline. Retain
baseline comparison as UNKNOWN until grounded comparable observations exist;
likewise do not infer usage/cost metrics. No merge, release or production
authority follows from this report.
