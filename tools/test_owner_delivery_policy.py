#!/usr/bin/env python3
"""Regression for owner-scoped delivery accountability, not a universal org policy."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def slice_section(text: str, heading: str, next_heading: str) -> str:
    """Check that intended portable section exists without scanning unrelated policy."""
    assert text.count(heading) == 1, f"missing/duplicate section: {heading}"
    assert text.count(next_heading) == 1, f"missing/duplicate section: {next_heading}"
    return text.split(heading, 1)[1].split(next_heading, 1)[0]


def violations(core: str, adoption: str, owner: str, template: str) -> list[str]:
    """Structural guard: consumer templates stay universal, owner profile has the choices."""
    findings: list[str] = []
    portable = (
        ("core", core, "## Delivery accountability and repository ownership",
         "## Provider-aligned policy layers"),
        ("adoption", adoption, "## Delivery responsibility before adoption",
         "## Adoption trigger"),
    )
    for name, body, start, end in portable:
        if body.count(start) != 1 or body.count(end) != 1:
            findings.append(f"{name}:DELIVERY_SECTION_MISSING")
            continue
        section = body.split(start, 1)[1].split(end, 1)[0]
        if any(marker in section for marker in ("datarelay-labs/*", "xdr-labs/*")):
            findings.append(f"{name}:ORG_CHOICE_IN_UNIVERSAL_POLICY")
        if not all(term in section.lower() for term in ("owner", "repo")):
            findings.append(f"{name}:OWNER_REPOSITORY_BOUNDARY_MISSING")
    if "datarelay-labs/*" not in owner or "xdr-labs/*" not in owner:
        findings.append("profile:ORG_OWNERSHIP_MAPPING_MISSING")
    if "**writes the existing project GitHub roadmap**" not in owner:
        findings.append("profile:DATARELAY_DELEGATION_MISSING")
    if "**Directly implements**" not in owner or "**Direct executor**" not in owner:
        findings.append("profile:DIRECT_EXECUTION_MISSING")
    if "its own prior unfinished Work Packet" not in owner:
        findings.append("profile:FIRST_ACTION_PRIORITY_LOST")
    if not all(s in owner.lower() for s in ("no", "release", "credential", "denial")):
        findings.append("profile:SAFETY_APPROVAL_BOUNDARY_MISSING")
    if any(mark in template for mark in ("## Owner-scoped XDR direct application",
                                         "datarelay-labs/*", "xdr-labs/*")):
        findings.append("template:OWNER_ORGANIZATION_LEAK")
    return findings


def main() -> int:
    core = (ROOT / "standards/CORE.md").read_text(encoding="utf-8")
    adoption = (ROOT / "standards/ADOPTION.md").read_text(encoding="utf-8")
    owner = (ROOT / ".engineering/owner-delivery.md").read_text(encoding="utf-8")
    template = (ROOT / "templates/AGENTS.md").read_text(encoding="utf-8")
    assert not violations(core, adoption, owner, template), violations(
        core, adoption, owner, template
    )
    # In-memory regressions prove that core leakage and role reversal fail closed.
    assert "core:ORG_CHOICE_IN_UNIVERSAL_POLICY" in violations(
        core.replace("## Delivery accountability and repository ownership",
                     "## Delivery accountability and repository ownership\ndatarelay-labs/*"),
        adoption, owner, template
    )
    assert "profile:DATARELAY_DELEGATION_MISSING" in violations(
        core, adoption, owner.replace("**writes the existing project GitHub roadmap**",
                                     "delegates source implementation"), template
    )
    assert "profile:DIRECT_EXECUTION_MISSING" in violations(
        core, adoption, owner.replace("**Direct executor**", "Roadmap-only"),
        template
    )
    assert "template:OWNER_ORGANIZATION_LEAK" in violations(
        core, adoption, owner, template + "\n## Owner-scoped XDR direct application"
    )
    print("OWNER_DELIVERY_POLICY_REGRESSION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
