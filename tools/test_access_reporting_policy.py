#!/usr/bin/env python3
"""Policy propagation regressions, not a claim of live-agent compliance."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from adopt import canonical_managed_policy_lines, plan_execution_policy_sync

ROOT = Path(__file__).resolve().parents[1]
BEFORE = "c2b51363cc216ce5a9962ee41f0fb1b53d8967bf"
MARKER = "- **Execution profile authority:**"


def text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_managed_discovery_instruction() -> None:
    policy = next(line for line in canonical_managed_policy_lines() if line.startswith(MARKER))
    for rel in ("AGENTS.md", "templates/AGENTS.md"):
        assert policy in text(rel), rel
    for required in (
        "discover the connected task-relevant tools",
        "attempt a minimal authorized action when exposed",
        "same-session, same-target, same-action",
        "fresh failure or scope change",
        "unattempted is not denied",
        "explicit tool denials remain binding",
    ):
        assert required in policy, required


def test_pre_repository_entrypoints() -> None:
    for rel in ("templates/CHATGPT_CUSTOM_INSTRUCTION.txt", "templates/CHATGPT_PROJECT_INSTRUCTION.txt"):
        content = text(rel)
        for required in (
            "discover the connected task-relevant tools",
            "attempt a minimal authorized action when exposed",
            "sandbox-only limitation does not establish remote unavailability",
            "same-session, same-target, same-action",
            "preserve approvals and explicit tool denials",
        ):
            assert required in content, (rel, required)
        assert content.index("discover the connected") < content.index("Work Packet"), rel


def test_reporting_and_security_boundaries() -> None:
    rules = text("standards/ENFORCEMENT.md")
    for required in (
        "no applicable action exposed",
        "invalid arguments",
        "transport/authentication failure",
        "observed permission denial",
        "explicit platform/tool block",
        "Successful reads prove reads, not writes",
        "Never route around an explicit denial",
        "what was not attempted",
        "sanitized error",
        "without an observed result supporting that cause",
        "not installed ChatGPT settings",
        "not live-agent compliance evidence",
    ):
        assert required in rules, required
    security = text("standards/SECURITY.md")
    assert "Unknown or unapproved tool provenance/capability must fail closed" in security
    assert "availability never grants authorization" in security
    assert "Never bypass an explicit denial" in security


def fixture(root: Path, content: str) -> None:
    (root / "AGENTS.md").write_text(content, encoding="utf-8")
    (root / ".engineering").mkdir()
    (root / ".engineering/project.yaml").write_text(
        "engineering_system:\n  baseline: " + BEFORE + "\n", encoding="utf-8"
    )


def previous_template() -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), "show", BEFORE + ":templates/AGENTS.md"], text=True
    )


def test_prior_template_upgrade_preserves_product_and_is_idempotent() -> None:
    product = "\n## Product restrictions\nProduction changes require owner approval.\n"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fixture(root, previous_template() + product)
        result = plan_execution_policy_sync(root)
        assert result is not None
        assert "unattempted is not denied" in result
        assert product in result
        (root / "AGENTS.md").write_text(result)
        assert plan_execution_policy_sync(root) is None


def test_interleaved_rule_upgrade_preserves_custom_suffix() -> None:
    product = "Production changes require owner approval."
    old = previous_template()
    line = next(line for line in old.splitlines() if line.startswith(MARKER))
    old = old.replace(line, line + " " + product)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fixture(root, old)
        result = plan_execution_policy_sync(root)
        assert result is not None and "unattempted is not denied" in result
        assert product in result
        (root / "AGENTS.md").write_text(result)
        assert plan_execution_policy_sync(root) is None


def test_unknown_custom_policy_is_not_silently_overwritten() -> None:
    old = previous_template()
    line = next(line for line in old.splitlines() if line.startswith(MARKER))
    old = old.replace(line, MARKER + " Require product-specific approval.")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fixture(root, old)
        try:
            plan_execution_policy_sync(root)
        except SystemExit as exc:
            assert "customized managed execution-profile policy" in str(exc)
        else:
            raise AssertionError("unrecognized product policy was silently replaced")
        assert (root / "AGENTS.md").read_text() == old


def run_checks() -> None:
    checks = (
        test_managed_discovery_instruction,
        test_pre_repository_entrypoints,
        test_reporting_and_security_boundaries,
        test_prior_template_upgrade_preserves_product_and_is_idempotent,
        test_interleaved_rule_upgrade_preserves_custom_suffix,
        test_unknown_custom_policy_is_not_silently_overwritten,
    )
    for check in checks:
        check()
    print(f"ACCESS_REPORTING_POLICY_TESTS=PASS checks={len(checks)} LIVE_AGENT_EVIDENCE=NOT_CLAIMED")


if __name__ == "__main__":
    run_checks()
