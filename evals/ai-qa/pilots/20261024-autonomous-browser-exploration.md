# P1 — Live AI-selected exploratory Chromium QA

Date: 2026-10-24. Canonical workstream: Engineering System Issue #317, Draft PR #318.

## Scope and evidence authority

Unlike a hardcoded Playwright E2E, this protocol maintains one real Chromium browser and requires the selected ChatGPT runtime to examine every fresh rendered observation, choose the next action and explain why. Each action is bound to the immediately preceding observation digest. Stale element references, destructive/credential actions, unapproved external origins and state changes are denied.

The same CHATGPT_CHAT runtime implemented and operated this pilot, using a separately frozen, requirements-first QA mission and the live browser UI to select actions. This is **model-guided interactive exploration**, not a cryptographically attested second LLM, standalone external provider invocation, unattended model agent or formal product release Full User E2E. Its authority remains T0_ADVISORY_ONLY, and external model independence is unverified.

Development Web target: https://app.parser.xdr.ooo/. Two synthetic firewall security JSON events per run, no customer log data. Browser network fence: same-origin GET/HEAD/OPTIONS, and POST only to /api/v1/multipass/preview-batch; no release, native parser activation, credentials or downstream sensor effects.

Frozen served HTML SHA-256 for both runs:
d117a7936de381f64c4caa6623d4f8483b6bdcacc7469b400dfec241bf6c19dd

First externally frozen requirements SHA-256:
063cdf4643fb180df50c4733c4f77c1168e7b749b5e6c19006b035ba99fe47d4

Second externally frozen requirements SHA-256:
fe04d7c185f37a87a209bf4649a47525373d3d133a4d85c71cc1ada2c2b2f25e

## First real exploration — discovered QA engine defect

Five ChatGPT-selected actions and six actual Chromium observations: first-use Go to log input, paste two distinct synthetic JSON events, Analyze Logs through the real server, Review Fields, and Map Fields. Actual public UI reported 2 of 2 parsed completely and both original events. Real preview-batch POST returned HTTP 200; no browser exception or blocked external request.

The explorer incorrectly considered off-viewport DOM controls visible. On the long normalization mapping page, the first 75 controls exhausted its UI element list and hid the valid bottom Back to Review Fields control. This is a **QA harness control-discovery bug**, NOT an observed Parser Studio product defect. First trial disposition: POTENTIAL_FINDING (AIQA-CONTROL-CAP-001).

Private report SHA-256:
e8e9742ad5a58cf2d8fc0faedc7620fcda2cbd4efe1fae8b419e14961fd24c7a

Real Chromium trace SHA-256:
2c6671a330391ecbd3aa4a72f9bcaceda6c3234ae2dcbb834b6bce54f3c7bb28

## Corrective action and second real browser exploration

Fixed viewport element classification, ensured fresh references after every scroll, and added visible select-element option support. Added regression tests for viewport intersection, safe dropdown choice, stale actions and forbidden PASS.

In a NEW real Chromium run the selected ChatGPT QA actor chose **10 actions from 11 fresh observations**. It pasted two newly selected synthetic firewall events, performed real analysis, scrolled to the actual Review Fields button, checked extracted fields, selected Event 2 from the observed HTML select options, confirmed distinct second-event values (192.0.2.102 and 198.51.100.45), entered Map Fields, scrolled past the long mapping list until the formerly hidden Back to Review Fields action was **actually visible**, then returned to Review Fields without creating any parser file or sending to a sensor.

The actual preview-batch API was HTTP 200; browser action failures 0, unauthorized/blocked network requests 0, browser JS errors 0. No new Parser Studio product bug was confirmed by this narrow test, which does NOT prove the whole product bug-free.

Private second final report SHA-256:
1bd8d06026f4894450dcd45387027cd9d3e8e107599baf8e82bfe2c8f9314b4b

Real second Chromium trace SHA-256:
47e53a78d23761bcd4246617f9cafc027be21d349166d841da68558c1a34d3d4

Second event ledger SHA-256:
78fc5a91d4abe782f0ae34dae4d50ba65a0b48e93977106ace56ed955521de30

Both runs passed an independent read-only artifact/causal chain audit with verdict EVIDENCE_CONSISTENT_T0. Neither human-equivalent release nor independent external provider identity was verified.

## How to execute future model-led exploration

1. Freeze a small externally specified synthetic-safe requirements mission (kind independent-ai-browser-mission) with SHA-256, allowlisted development origin, bounded action count and expected observable outcomes.
2. Pin current served HTML digest independently and provide an unused private output directory.
3. Start the Node adapter adapters/ai-qa/autonomous-browser-explorer.cjs with --mission, --expected-plan-sha256, --expected-html-sha256 and --out. Set PLAYWRIGHT_MODULE_ROOT to the existing pinned Playwright installation if needed; nothing is installed automatically.
4. Read AIQA_READY and the actual AIQA_OBSERVATION visible element IDs/screenshot summary. ChatGPT must choose a new JSON action from THIS observation, explain why, and read the following observation before deciding again. Supported safe actions: click, fill, select, press, scroll and back.
5. Finish explicitly as NO_CONFIRMED_FINDING, POTENTIAL_FINDING, UNVERIFIED or BLOCKED. PASS and product release claims are forbidden.
6. Independently run tools/ai_qa_explorer_audit.py with the frozen requirements and served HTML SHA values. This checks causal action sequencing, evidence integrity and screenshots/trace, not independent actor attestation or semantic truth of all product behavior.

Private evidence root on dev-atlas:
 /home/aella/.local/state/engineering-ai-qa-autonomous-20261024/

## Remaining boundaries

This pilot is selected ChatGPT-in-the-loop exploration, **not** a continuously running external LLM autonomy service. No Midscene, commercial QA account or external OpenAI API was invoked. A separate provider model connection, an independently trusted agent identity, broader hidden-bug benchmarks and any full product human-equivalent release gate remain distinct work. The actual deployed Web build-to-source Git HEAD was not independently bound by this HTML pin and must not be inferred.
