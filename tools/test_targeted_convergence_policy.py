#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = {
    "standards/CORE.md": [
        "full qualification is not a default PR gate and MUST NOT be restarted after every small fix",
        "after affected convergence is clean",
    ],
    "standards/TESTING.md": [
        "Do not restart a long full suite after every individual fix",
        "one brand-new **complete confirmation run**",
        "Do **not** restart the complete gate after every individual fix",
    ],
    "standards/RELEASE.md": [
        "affected scenario/parity reruns until frozen findings are clean",
        "Do **not** restart the complete user gate after every individual fix",
        "one brand-new **complete confirmation run**",
    ],
    "standards/USER_ACCEPTANCE.md": [
        "affected scenario/journey reruns until every frozen finding is targeted-clean",
        "complete gate MUST NOT be restarted after every individual fix",
        "after the remediation batch is targeted-clean",
    ],
    "templates/AGENTS.md": [
        "do not restart broad/full regression after every fix",
        "do not restart the complete gate after every individual fix",
    ],
    "templates/CHATGPT_PROJECT_INSTRUCTION.txt": [
        "targeted/affected rerun until the changed slice is clean",
        "complete discovery, targeted affected remediation/reruns, then one new complete confirmation run",
    ],
}

for rel, needles in REQUIRED.items():
    text = (ROOT / rel).read_text(encoding="utf-8")
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"FAIL targeted convergence policy drift: {rel} missing {needle!r}")

print("TARGETED_CONVERGENCE_POLICY=PASS")
