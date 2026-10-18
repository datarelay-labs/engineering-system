# External standards and official guidance — informative alignment

Reviewed: 2026-10-18. The machine-readable catalog is [external-reference-map.yaml](external-reference-map.yaml). This page **does not assert certification, clause-by-clause coverage, a NIST endorsement, a SLSA level, or completed live-agent testing**. Public abstracts and official free guidance were reviewed; paid ISO full texts were not acquired. Mapping an existing policy file is **not proof that the policy is executed or that a product conforms**.

## Decision and scope

Engineering System is a lightweight, provider-neutral engineering control layer for an AI-assisted solo developer. Its existing lifecycle, Work Packet, testing, authority and release gates remain authoritative. Adopt external references **as design/assessment inputs**; do not install a separate compliance framework, duplicate CI, create one Issue per standard, or impose new blanket human-approval stages. Preserve explicit owner decisions, high-risk approvals, root-of-trust gates and existing product-specific stricter rules.

## Traceability crosswalk (public-source level)

| Reference | Official source | Existing canonical contract | Assessment / real gap |
| --- | --- | --- | --- |
| ISO/IEC/IEEE 12207:2026 | [ISO](https://www.iso.org/standard/90219.html) | [CORE](CORE.md), [DEVELOPMENT](DEVELOPMENT.md), [OPERATIONS](OPERATIONS.md) | Lifecycle addressed; process-by-process conformance **not assessed** |
| ISO/IEC/IEEE 29148:2018 | [ISO](https://www.iso.org/standard/72089.html) | [DESIGN](DESIGN.md), [SESSION_CONTINUITY](SESSION_CONTINUITY.md) | Requirements and completion contracts exist; **measured** requirement–test–evidence traceability missing |
| ISO/IEC 25010:2023 | [ISO](https://www.iso.org/standard/78176.html) | [QUALITY](QUALITY.md), [USER_ACCEPTANCE](USER_ACCEPTANCE.md) | Quality gates exist; nine product quality characteristics are not individually assessed |
| ISO/IEC 25023:2016 | [ISO](https://www.iso.org/standard/35747.html) | [QUALITY](QUALITY.md), [TESTING](TESTING.md) | Select project-relevant quality measures; no global score |
| ISO/IEC/IEEE 29119-2:2021 | [ISO](https://www.iso.org/standard/79428.html) | [TESTING](TESTING.md), [RELEASE](RELEASE.md) | Test stages/scenarios exist; process-level completeness not audited |
| NIST SP 800-218 SSDF 1.1 | [NIST final](https://csrc.nist.gov/pubs/sp/800/218/final) | [SECURITY](SECURITY.md), [RELEASE](RELEASE.md) | Policy present; deployed secure-development practice evidence incomplete |
| NIST AI RMF 1.0 | [NIST](https://airc.nist.gov/airmf-resources/airmf/) | [PROVIDER_GUIDANCE](PROVIDER_GUIDANCE.md), [SECURITY](SECURITY.md) | GOVERN/MAP/MEASURE/MANAGE relevant; **live-agent measurement pending** |
| OWASP Agentic Top 10 2026 | [OWASP](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) | [SKILLS](SKILLS.md), [SECURITY](SECURITY.md), [ENFORCEMENT](ENFORCEMENT.md) | Trust boundaries exist; no full ASI01–ASI10 threat test coverage |
| OWASP Agent Control Standard (2026) | [OWASP](https://genai.owasp.org/resource/agent-control-standard-acs/) | [SKILLS](SKILLS.md), [SECURITY](SECURITY.md) | Conditional host-hooks control reference; **native Chat hook availability not established** |
| Model Context Protocol (2026-07-28) | [MCP](https://modelcontextprotocol.io/specification/2026-07-28) | [SECURITY](SECURITY.md), [SKILLS](SKILLS.md) | Conditional owned MCP server/client conformance, not asserted for external integrations |
| SLSA 1.2 | [SLSA](https://slsa.dev/spec/v1.2/) | [SECURITY](SECURITY.md), [RELEASE](RELEASE.md) | Source hashes/SBOM/provenance policy **do not establish** a SLSA level |
| ISO/IEC 42001:2023 | [ISO](https://www.iso.org/standard/81230.html) | [PROVIDER_GUIDANCE](PROVIDER_GUIDANCE.md) | Formal management-system adoption is conditional and out of current scope |
| ISO/IEC 5338:2023 | [ISO](https://www.iso.org/standard/81118.html) | [CORE](CORE.md) | Applies when developing AI systems, not automatically to using an AI coding assistant |
| DORA metrics | [DORA](https://dora.dev/guides/dora-metrics/) | [QUALITY](QUALITY.md), [PROVIDER_GUIDANCE](PROVIDER_GUIDANCE.md) | Use outcome measurement philosophy, not DORA label for invented agent metrics |

**Version caveats:** ISO 12207:2026 supersedes 2017. ISO 29148:2018 and ISO 25023:2016 are still published while revisions are in development. NIST SSDF v1.1 is the current *final* publication; the v1.2 revision is a **draft**, not the final baseline. NIST AI RMF 1.0 is being updated. SLSA 1.2 is approved and uses separate Build/Source tracks. Check authoritative publication status again before changing the referenced baseline.

## P0 evidence trace: reference → test scenario (metadata only)

The original reference map included `tools/validate.py` (a broad validation runner) and `tools/behavior_eval.py` (an evaluation implementation) under `evidence_paths`, even though neither was a **direct test scenario**. This created a misleading candidate-evidence link. The former is now explicitly unmapped, and the latter is replaced by the existing directly registered `tools/test_behavior_eval.py` scenario.

Run `python3 -B tools/test_external_references.py --report` for a deterministic JSON **manifest trace**, separate from the normal catalog integrity test. For each source it lists *candidate test file → declared scenario ID*, when a direct selection exists. A scenario being configured **does not prove it ran**, that its assertion establishes the standard requirement, or that any ISO/NIST/SLSA conformance level is met. Accordingly `exact_head_test_evidence=NOT_ESTABLISHED`, `live_agent_evidence=NOT_VERIFIED`, and `conformance_evidence=NOT_ESTABLISHED` remain explicit. The report is read-only and creates no duplicate CI, release requirement or product Work Packet.

If a path is not registered as a directly invocable test in the canonical `.engineering/tests.yaml`, the integrity test fails; quoting a script in a shell comment, echo, wrapper, or `|| true` does not count. This strengthens the crosswalk's narrow *catalog traceability*, not the still-unmeasured full *requirement → independently observed exact-HEAD behavior* traceability.

## P0 — execute without adding ceremony

1. **Requirement → observable result.** For a material change, the existing accepted requirement/Work Packet completion criterion, affected scenario ID and the actual outcome at the relevant exact HEAD should be traceable through existing artifacts. Start by measuring gaps; do not mandate a new requirements file or checklist for every small fix. Unknown coverage is not completion.
2. **Agent risk → targeted regression.** Map risks relevant to the actual tool privileges and actions (OWASP ASI01 goal hijack, ASI02 tool misuse, ASI03 privilege abuse, ASI04 supply-chain compromise, ASI05 unexpected code execution) to existing trust-boundary and CI checks. Add a test only for a demonstrated uncovered, reachable failure mode. External tool output never grants authority.
3. **Real Chat effectiveness.** Keep [Issue #309](https://github.com/datarelay-labs/engineering-system/issues/309) as the authoritative 30-run product canary. At the latest authenticated audit, **0/30** qualified runs were evidenced; B5 improvement is **UNKNOWN**. Continue only when real product Chats produce observations. No synthetic Chat session, fabricated denominator, prose assertion or deterministic unit test is live-eval PASS.

Agent-specific **proposed** indicators are priority-first-action fidelity, successful next-chat resume, uninterrupted eligible roadmap continuation, owner re-instruction frequency, false blocker/false COMPLETE and verified task completion. These are *not* the DORA five metrics. Each metric must define an observation source, denominator and exclusion criteria; report **UNKNOWN**, not 0% or 100%, when real observations/baselines are absent.

### OWASP Agentic Top 10: risk-to-existing-contract assessment

This is **policy coverage**, not exploit-test or live-runtime PASS. The table names candidate safeguards and the still-unverified risk category; only reachable effects should receive new regressions. The full OWASP category names are from its published 2026 Top 10.

| OWASP class | Existing candidate control | What remains unverified |
| --- | --- | --- |
| ASI01 Agent Goal Hijack | [SESSION_CONTINUITY](SESSION_CONTINUITY.md), [ENFORCEMENT](ENFORCEMENT.md) | Live malicious prompt/tool output cannot override trusted owner scope |
| ASI02 Tool Misuse | [SKILLS](SKILLS.md), [SECURITY](SECURITY.md) | All reachable external-write tools honor concrete scope/approval |
| ASI03 Identity & Privilege Abuse | [SKILLS](SKILLS.md), [SECURITY](SECURITY.md) | Identity binding across actual connected systems |
| ASI04 Agentic Supply Chain Vulnerabilities | [SECURITY](SECURITY.md) | Plugin/MCP provenance and compromised-provider cases |
| ASI05 Unexpected Code Execution | [SKILLS](SKILLS.md), [ENFORCEMENT](ENFORCEMENT.md) | Tool command dispatch and arbitrary code boundary at host runtime |
| ASI06 Memory & Context Poisoning | [KNOWLEDGE](KNOWLEDGE.md), [SESSION_CONTINUITY](SESSION_CONTINUITY.md) | Actual fresh-chat behavior under poisoned derived context |
| ASI07 Insecure Inter-Agent Communication | [SKILLS](SKILLS.md), [PROVIDER_GUIDANCE](PROVIDER_GUIDANCE.md) | Conditional: only if inter-agent messaging is activated |
| ASI08 Cascading Failures | [ENFORCEMENT](ENFORCEMENT.md), [OPERATIONS](OPERATIONS.md) | Cross-tool fan-out failure containment / ambiguous retries |
| ASI09 Human-Agent Trust Exploitation | [RELEASE](RELEASE.md), [USER_ACCEPTANCE](USER_ACCEPTANCE.md) | Human approval decisions not misled by unsupported PASS claims |
| ASI10 Rogue Agents | [CORE](CORE.md), [SECURITY](SECURITY.md) | Real-agent continuation, scope drift and stopping behavior |

## P1 / P2 — conditional follow-through

- **P1:** Use existing [TESTING](TESTING.md) scenario registry to audit sampled 29119 test process coverage; use existing [QUALITY](QUALITY.md) evidence for selected 25010/25023 measures. Do not run whole release suites just to prove standards mapping.
- **P1:** Audit NIST SSDF final 1.1 at practice level when secure-development gaps are suspected. For SLSA, verify an **actual artifact**, hosted build provenance, verifier trust and source/build track requirements before any level claim. Generic SHA/SBOM generation cannot qualify a level.
- **P2:** Apply ISO 42001 organizational obligations and ISO 5338 AI-product lifecycle processes only upon explicit applicability. No new AIMS, governance committee, provider runtime, database, mandatory auditor, or blanket release gate.

## Deterministic validation and limits

`python3 tools/test_external_references.py` rejects broken canonical paths, malformed official-source references, unsupported status/priority, duplicate identities and any self-asserted conformance claim. This checks **crosswalk integrity only**, not the actual contents of paid standards, runtime controls, test execution, certification, or measured developer productivity. CI evidence is exact-HEAD, while issue-observed live-agent outcomes stay independent.

A new rule is justified only if an independently observed failure mode remains uncovered by existing contracts. Otherwise mark the reference **covered by existing policy**, **not assessed**, or **deferred** and stop.
