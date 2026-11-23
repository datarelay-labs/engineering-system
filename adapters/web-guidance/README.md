# Selective Modern Web Guidance — optional web consumer review

This is a read-only, opt-in nonproduction diagnostic for a user-facing web/browser consumer. It does not change canonical Engineering System release, quality, security, Full User E2E, or ownership rules. Non-web projects must not run this workflow.

## Pinned packages

modern-web-guidance version 1.0.1 (Apache-2.0), @axe-core/playwright version 4.14.0 (MPL-2.0) and axe-core, with exact package integrity in package-lock.json. Use Node >=22.12 and a disposable directory with npm ci --ignore-scripts --no-audit --no-fund. Never install globally, send source externally, or use an authenticated browser session.

## Manual workflow

Read the target repository AGENTS.md and .engineering/project.yaml first. Verify its actual Git HEAD and that its primary user surface is web/browser.

Execute the pinned official tool: modern-web-guidance legacy-scan /absolute/consumer/src --json, storing the JSON only in a private temporary location. This reports selected JavaScript modernization patterns, NOT WCAG. An ES2023 toSorted() suggestion can change sparse-array semantics or compatibility and is never an automatic parser fix.

For browser accessibility, run pinned axe-core in an isolated headless Chrome session against an unauthenticated localhost-only build; block all external network navigation. Capture only axe IDs, severity, node counts, source Git HEAD, and explicit browser network boundary; never raw DOM/HTML, URLs, query parameters or secrets.

Run:
python3 tools/web_guidance_contract.py --repo-root /absolute/consumer --source-sha EXACT_40_SHA --legacy-scan /tmp/legacy-scan.json --axe-receipt /tmp/axe-receipt.json

The evaluator compares actual Git HEAD and web consumer policy, checks evidence limits and provenance, strips content and source-line data, and returns review-only JSON. Omitting axe means WCAG_NOT_TESTED, not PASS.

## Trust boundary

Output always declares mutation_authority NO, modernization REVIEW_ONLY_NO_AUTOFIX, full_wcag_conformance NOT_ESTABLISHED, full_user_e2e NOT_ESTABLISHED, canonical_release_gate UNCHANGED. It is an advisory diagnostic, not a new runtime permission, auto-merge authority, release certification, or product bug fix. Remediation must occur under the product-owning current source/Work Packet after browser and functional regression testing.

Real consumer pilot and provenance: evals/ai-qa/pilots/20261124-modern-web-guidance-consumer.md.
Tests: python3 -B tools/test_web_guidance_contract.py.
