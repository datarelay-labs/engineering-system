# Parser Studio — first actual independent QA pilot (2026-10-24)

## Provenance and declared limits

- **Product:** xdr-labs/parser-studio; existing development-only https://app.parser.xdr.ooo/; no source change, deployment, production sensor, credential or actual Stellar target import.
- **Acting runtime:** CHATGPT_CHAT itself launched fresh real Chromium via existing dev-atlas Playwright and navigated the actual public user surface. This is a **bounded independent QA pilot**, NOT the product's complete formal Surface Reconciliation or Full User E2E release gate.
- **Frozen mission:** private dev-atlas /home/aella/.local/state/engineering-ai-qa-pilot-20261024/mission.json, externally frozen SHA-256 d2b90f0c77f1e9ccf622ca660400a7711b4ddd7f20eca7bc2a1b63db2a9db70f. Two distinct explicitly synthetic security JSON records, zero customer data, independently expected original values and final DRAFT outcome.
- **Observed live Web:** index.html SHA-256 64b65cb53d70ae3a7ce5c82f7bd4146e35aae7c0e9f7b5c28fe1ecc9e8237d13; index-sAXgzivN.js SHA-256 49bfa534a3d53d82784d69777956e64496caaad2524742145988569c6563fad7. These equal the earlier product-owner composite development release evidence. Git product main != deployed composite 154+155+156; do not assert product main HEAD or CI qualification.
- **Backend:** actual public /api/v1/multipass/preview-batch and /api/v1/parser-pairs/... HTTP endpoints. No third-party LLM request or browser JavaScript errors in the recorded run.

## P1 actual observed user mission / oracle

1. Open public Auto Parser in a fresh Chromium browser and paste the original two records; verify textarea has exact original bytes.
2. Click Analyze Logs. Actual backend returns 2 events with SOURCE_PARSED / JSON and original source SHA-256; browser summary states 2/2 parsed.
3. Click Review Fields → Map Fields → Create Parser; select Create Parser Files. Real server responses reported successful, actual generated files shown as **DRAFT not installed**.
4. Download the actual customized_demo_firewall and customized_demo_firewall.conf files.
5. Independently run adapters/ai-qa/parser-studio-oracle.py against frozen mission, actual HTTP capture, real images/trace and downloaded bytes, not just the UI success banner.

**Result:** INDEPENDENT_ORACLE_PASS (T0_ADVISORY_ONLY), 2/2 original records, 14/14 exact extracted field values **and byte-offset spans**, two generated files with SHA-256, actual Chromium trace, errors 0, unexpected third-party traffic 0.

**Evidence SHA-256 (private, not committed):**
- capture.json: 1c31aaee4e0ffaf3e023b019058a34e6ae9d5135034114ba0a1e344740a48f2b
- draft-run.json: 7ef84f94bf3fe277af7df825b7dd7fd6d11c0f56dc83e4cb38e3ad695db58fe9
- chromium-trace.zip: bc4f618dc87222add5a7f9392f25c0671722d0d8a9e75a3b8d996ebb40b8979e

## P2 bounded real metamorphic/property probe

- Browser-based same-origin development API, deterministic seed 20261024, **18 paired scenarios** (2 record orders), 36 HTTP analyses, exact original 504 extracted values and 504 field byte spans.
- Checked source/event SHA-256, source byte lengths, every event boundary and reversible record ordering. **18/18 pairs PASS; failures 0**.
- Private reproducible report: property-seed20261024.json SHA-256 431f05de02a9cf215ee1789633a563d635a567bb832b3704af8067631a13fbdf.
- This is a *custom seeded property/metamorphic implementation*. It is not Hypothesis, fast-check, Schemathesis, source mutation or chaos networking.

## P2 bounded semantic evidence mutation

- Injected 12 different faults into **copies of actual HTTP observations**, including record loss, field rewrites, source SHA, UTF-8 spans and false native verification. Recomputed altered response hashes so the oracle must detect semantics, not merely checksum mismatch.
- The independent oracle rejected **12/12 semantic mutants**, survived 0.
- Private report semantic-mutation-score.json SHA-256 df85bf6e8df27748500500c6ba0009790f1dab9656e4b06d3b26021fef98e896.
- **Not a product-source-code mutation score**; a separate Stryker/Hypothesis/real producer stateful fault campaign is needed for that claim.

## P2 actual isolated source mutation on the Engineering System QA engine

- Ran the canonical QA benchmark's **31 normal regression tests** as a green control, then introduced **8 one-at-a-time changes to its real Python source code in disposable temporary directories**. No product or permanent source file was edited.
- All **8/8 valid source mutants were killed**, survived 0; candidate test runs were isolated, with 31 assertions exercised by each candidate run. This is a real but deliberately **narrow QA-engine source mutation score**; it does NOT measure Parser Studio, Link or Control product-code mutation coverage and is not Stryker.
- Private report source-mutation-score.json SHA-256 2d51a6de433ea43f2f4292c404ab14ba9d4e14449922e49fb11204c7a495827d.

## P3/P4 remaining qualification boundaries

Parser Studio pilot source-field extraction and DRAFT generation are qualified on this narrow sample; *independent external ChatGPT model first-response success* and Stellar 7.0 installed/imported target remain NOT_VERIFIED. Other Link/Control/Grant/Atlas actual product pilots remain separately pending under their owner and test-environment constraints. For commercial QA platforms, the pinned bakeoff comparator and synthetic unit fixtures exist but no live QA.tech/TestSprite/Momentic accounts or runs were executed, and no prices or external vendor defect-detection scores are verified.

No release, merge, operational data mutation, real email approvals or production fault injection occurred.
