#!/usr/bin/env python3
"""Project skill cannot grant more than the canonical read-only permission."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_scoped_project_skill():
    metadata = yaml.safe_load((ROOT / ".engineering/skills.yaml").read_text())
    assert metadata["version"] == 1
    assert "profiles" not in metadata and "hooks" not in metadata
    assert len(metadata["skills"]) == 1
    skill = metadata["skills"][0]
    assert skill["id"] == "worktree-source-audit"
    assert skill["scripts"] == ["tools/lifecycle_audit.py"]
    assert skill["resources"] == ["standards/SESSION_CONTINUITY.md"]
    body = (ROOT / skill["body"]).read_text()
    assert "repo_read" in body and "CLOSED UNMERGED" in body
    assert "CANDIDATE_REVIEW" in body
    assert "fail closed" in body and "--apply" in body

    spec = importlib.util.spec_from_file_location(
        "skills_contract", ROOT / "tools/skills-contract.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules["skills_contract"] = module
    spec.loader.exec_module(module)
    report = module.check_contract(ROOT)
    assert report["skills_contract"] == "PRESENT"
    assert report["result"] == "PASS", report["findings"]
    assert report["scripts_grant_execution"] is False
    assert report["resources_executable"] is False
    assert report["hooks_executable"] is False
    # Default repo_read is intentionally unchanged and cannot call shell/network.
    assert set(report["profiles"]["repo_read"]["allowed"]) == {"read"}


if __name__ == "__main__":
    test_scoped_project_skill()
    print("WORKTREE_SKILL_PERMISSION_SCOPE=PASS")
