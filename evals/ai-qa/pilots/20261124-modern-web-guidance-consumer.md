# ES-TOOL-3 — real Modern Web Guidance consumer qualification

Date: 2026-11-24 KST.

Source: Parser Studio historical *nonproduction* clean development checkout xdr-labs/parser-studio at 4833c557c077e253be1768083a1b5f1b4a0e760d. This is NOT the current isoc-labs/parser-studio main, which was independently observed at 8f2a3ec7ad575d62724f23dc8385815d2d56701d. Do not claim the findings exist in production or that they have been fixed.

- Existing UI regression baseline: 3 Vitest files, 9/9 PASS; actual process returned exit 0.
- Official modern-web-guidance 1.0.1: actual old product src scan yielded 9 JavaScript modernization findings, all rule js/copying-array-methods, across 52 deterministic rules. No accessibility conclusions can be drawn from this scanner alone. No blanket toSorted migration: accuracy and ES2023/sparse-array behavior unqualified.
- Actual nonproduction browser: isolated Chrome for Testing 156 with pinned @axe-core/playwright 4.14.0, Vite localhost port 43981 and external navigation blocked. Automated WCAG A/AA tags found 5 violation rules, 23 affected DOM elements: aria-prohibited-attr serious 1; color-contrast serious 15; label-content-name-mismatch serious 3; nested-interactive serious 2; target-size serious 2. 22 automated checks PASS. Incomplete manual checks remain.
- Modern scanner JSON SHA256: 8d6bcc0288eb70c7615f7138a264f325728d2135e09b6cf5b036b27ba74f7e1a.
- Browser axe receipt SHA256: 6b52844e54ba2767ce4688ae485bb167c56a7e3264471b798766fe9b502619db.
- Isolated npm lock SHA256: 1073811db1151b165f3e96f097c289a1e5b269a726d89bd0de6b9a4bb0316a27.
- The actual Git-bound report was successfully generated from the source checkout: engineering.optional-web-guidance.v1, WCAG ISSUES_OBSERVED 23 nodes, 9 modernization suggestions, NO mutation authority.
- No browser login, customer data, source publication, permissions change, production action, or product code mutation.

Decision: Adopt only an OPTIONAL, web-only, privacy-minimized report normalization and pinned local toolchain. Do not add duplicate release gates, apply unrelated JS modernizations or claim full WCAG/Full User E2E PASS. Owning Parser Studio project must inspect current isoc-labs source, repeat the browser scan, and implement any independently reproduced problems with focused keyboard/contrast tests and accuracy regression.
