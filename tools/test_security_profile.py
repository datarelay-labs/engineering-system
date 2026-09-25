#!/usr/bin/env python3
"""Regressions for the read-only repository security-profile auditor."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "security-profile.py"
SHA = "a" * 40
BANNED = (
    "subprocess",
    "urlopen",
    "urllib",
    "requests",
    "os.system",
    "shell=True",
    "api.github.com",
    "socket",
)


def load_tool():
    spec = importlib.util.spec_from_file_location("security_profile", TOOL)
    if spec is None or spec.loader is None:
        raise SystemExit("FAIL cannot load security-profile.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROFILE = load_tool()


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def write_project(
    root: Path,
    *,
    maturity: str,
    production_oriented: bool | None,
    pre_product: bool = False,
) -> None:
    operations: dict[str, object] = {}
    if production_oriented is not None:
        operations["production_oriented"] = production_oriented
    if pre_product:
        operations["pre_product"] = True
    document = {
        "engineering_system": {"version": "1.6.5"},
        "project": {"name": "fixture", "type": "app", "maturity": maturity},
        "domains": ["standards"],
        "operations": operations,
    }
    path = root / ".engineering"
    path.mkdir(parents=True, exist_ok=True)
    (path / "project.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")


def write_workflow(root: Path, name: str, body: str) -> None:
    path = root / ".github" / "workflows"
    path.mkdir(parents=True, exist_ok=True)
    (path / name).write_text(body, encoding="utf-8")


def enabled_fixture(**extra: object) -> dict[str, object]:
    fixture: dict[str, object] = {
        "repository": "example/app",
        "visibility": "private",
        "secret_scanning": "enabled",
        "push_protection": "enabled",
        "dependabot_security_updates": "enabled",
        "codeql_default_setup": "configured",
        "privileged_tools": [],
    }
    fixture.update(extra)
    return fixture


def state_of(report: dict, control_id: str) -> str:
    matches = [item["state"] for item in report["controls"] if item["id"] == control_id]
    if len(matches) != 1:
        fail(f"{control_id} missing from {report['controls']}")
    return matches[0]


def test_profiles() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_project(root, maturity="production", production_oriented=True)
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        report = PROFILE.build_report(root, enabled_fixture())
        if report["profile"] != "production-code":
            fail(f"production profile was {report['profile']}")

        dev = Path(tmp) / "dev"
        dev.mkdir()
        write_project(dev, maturity="development", production_oriented=False)
        (dev / "src").mkdir()
        (dev / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        report = PROFILE.build_report(dev, None)
        if report["profile"] != "development-code":
            fail(f"development profile was {report['profile']}")

        docs = Path(tmp) / "docs"
        docs.mkdir()
        write_project(docs, maturity="development", production_oriented=False)
        (docs / "mkdocs.yml").write_text("site_name: docs\n", encoding="utf-8")
        (docs / "docs").mkdir()
        (docs / "docs" / "index.md").write_text("# Docs\n", encoding="utf-8")
        report = PROFILE.build_report(docs, enabled_fixture())
        if report["profile"] != "docs-site":
            fail(f"docs profile was {report['profile']}")

        empty = Path(tmp) / "empty"
        empty.mkdir()
        write_project(empty, maturity="experimental", production_oriented=False, pre_product=True)
        (empty / "README.md").write_text("bootstrap\n", encoding="utf-8")
        report = PROFILE.build_report(empty, enabled_fixture())
        if report["profile"] != "empty-preproduct":
            fail(f"empty profile was {report['profile']}")

        conflict = Path(tmp) / "conflict"
        conflict.mkdir()
        write_project(conflict, maturity="development", production_oriented=True)
        (conflict / "src").mkdir()
        (conflict / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        report = PROFILE.build_report(conflict, None)
        if report["profile"] != "NEEDS_INPUT" or "conflicting required facts" not in report["reason"]:
            fail(f"conflict profile was {report['profile']} {report['reason']}")

        missing = Path(tmp) / "missing"
        missing.mkdir()
        (missing / "README.md").write_text("no facts\n", encoding="utf-8")
        report = PROFILE.build_report(missing, None)
        if report["profile"] != "NEEDS_INPUT":
            fail(f"missing facts profile was {report['profile']}")


def test_sensitive_action_refs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_project(root, maturity="production", production_oriented=True)
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        write_workflow(
            root,
            "release.yml",
            "name: release\njobs:\n  publish:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses: actions/checkout@v4\n",
        )
        report = PROFILE.build_report(root, enabled_fixture())
        if state_of(report, "sensitive_action_pin") != "REQUIRED_FAIL":
            fail("tag-pinned sensitive action was not REQUIRED_FAIL")
        write_workflow(
            root,
            "release.yml",
            "name: release\njobs:\n  publish:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses: actions/checkout@main\n",
        )
        report = PROFILE.build_report(root, enabled_fixture())
        if state_of(report, "sensitive_action_pin") != "REQUIRED_FAIL":
            fail("branch-pinned sensitive action was not REQUIRED_FAIL")
        write_workflow(
            root,
            "release.yml",
            "name: release\njobs:\n  publish:\n    runs-on: ubuntu-latest\n    steps:\n"
            f"      - uses: actions/checkout@{SHA}\n"
            "      - uses: ./.github/actions/local\n",
        )
        report = PROFILE.build_report(root, enabled_fixture())
        if state_of(report, "sensitive_action_pin") != "REQUIRED_PASS":
            fail("full SHA sensitive action did not pass")
        write_workflow(
            root,
            "release.yml",
            "name: release\njobs:\n  publish:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses: not a valid action\n",
        )
        report = PROFILE.build_report(root, enabled_fixture())
        if state_of(report, "sensitive_action_pin") != "REQUIRED_FAIL":
            fail("malformed sensitive action silently passed")


def test_docs_missing_codeql_is_not_a_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_project(root, maturity="development", production_oriented=False)
        (root / "mkdocs.yml").write_text("site_name: docs\n", encoding="utf-8")
        (root / "docs").mkdir()
        (root / "docs" / "index.md").write_text("# Docs\n", encoding="utf-8")
        fixture = enabled_fixture(codeql_default_setup="not-configured")
        report = PROFILE.build_report(root, fixture)
        if report["profile"] != "docs-site":
            fail("docs fixture changed profile")
        if state_of(report, "codeql_or_sast") != "NOT_APPLICABLE":
            fail("docs CodeQL absence was not NOT_APPLICABLE")
        if any(item["state"] == "REQUIRED_FAIL" and item["id"] == "codeql_or_sast" for item in report["controls"]):
            fail("docs fixture failed because CodeQL was absent")


def test_empty_controls_are_deferred() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_project(root, maturity="experimental", production_oriented=False, pre_product=True)
        report = PROFILE.build_report(root, enabled_fixture(codeql_default_setup="configured"))
        if report["profile"] != "empty-preproduct":
            fail("preproduct profile changed")
        states = {item["state"] for item in report["controls"]}
        if states != {"DEFERRED"}:
            fail(f"empty/preproduct fabricated states {states}")


def test_missing_visibility_is_unavailable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_project(root, maturity="production", production_oriented=True)
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        fixture = enabled_fixture()
        del fixture["visibility"]
        report = PROFILE.build_report(root, fixture)
        if state_of(report, "repository_visibility") != "UNAVAILABLE":
            fail("missing visibility was not UNAVAILABLE")
        if state_of(report, "secret_scanning") == "REQUIRED_PASS":
            fail("unknown visibility became PASS")


def test_equivalent_evidence() -> None:
    valid = {
        "control_id": "codeql_or_sast",
        "provider": "example-sast",
        "immutable_id": "scanner-1.2.3",
        "evidence_ref": "evidence/sast/2026-09-25",
        "timestamp": "2026-09-25T00:00:00Z",
        "repository": "example/app",
        "approval_state": "approved",
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_project(root, maturity="production", production_oriented=True)
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        (root / "README.md").write_text(
            "EQUIVALENT_EXTERNAL provider=example-sast control_id=codeql_or_sast\n",
            encoding="utf-8",
        )
        prose = PROFILE.build_report(root, enabled_fixture(codeql_default_setup="not-configured"))
        if state_of(prose, "codeql_or_sast") != "REQUIRED_FAIL":
            fail("repository prose granted EQUIVALENT_EXTERNAL")
        accepted = PROFILE.build_report(
            root,
            enabled_fixture(codeql_default_setup="not-configured", equivalent_external=[valid]),
        )
        if state_of(accepted, "codeql_or_sast") != "EQUIVALENT_EXTERNAL":
            fail("valid equivalent evidence was rejected")
        invalid = dict(valid)
        invalid["approval_state"] = "pending"
        rejected = PROFILE.build_report(
            root,
            enabled_fixture(codeql_default_setup="not-configured", equivalent_external=[invalid]),
        )
        if state_of(rejected, "codeql_or_sast") == "EQUIVALENT_EXTERNAL":
            fail("invalid equivalent evidence was accepted")


def test_unknown_privileged_provenance_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_project(root, maturity="production", production_oriented=True)
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        unknown = enabled_fixture(
            privileged_tools=[{"id": "mcp.deploy", "privileged": True, "provenance": "unknown"}]
        )
        report = PROFILE.build_report(root, unknown)
        if state_of(report, "privileged_tool_provenance") != "REQUIRED_FAIL":
            fail("unknown privileged provenance did not fail closed")
        known = enabled_fixture(
            privileged_tools=[
                {
                    "id": "mcp.deploy",
                    "privileged": True,
                    "provenance": "approved",
                    "provider": "example",
                    "immutable_id": "tool-1",
                }
            ]
        )
        report = PROFILE.build_report(root, known)
        if state_of(report, "privileged_tool_provenance") != "REQUIRED_PASS":
            fail("explicit privileged provenance did not pass")


def test_outcome_has_no_mutation_path() -> None:
    source = TOOL.read_text(encoding="utf-8")
    for token in BANNED:
        if token in source:
            fail(f"security-profile.py contains banned token {token}")
    if PROFILE.command_names() != ("classify", "audit"):
        fail(f"unexpected commands {PROFILE.command_names()}")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_project(root, maturity="development", production_oriented=False)
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        fixture = Path(tmp) / "fixture.json"
        fixture.write_text(json.dumps({"apply": True}), encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(TOOL), "audit", "--root", str(root), "--github-fixture", str(fixture)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode == 0:
            fail("mutation fixture was accepted")
        if "unsupported fields" not in completed.stderr:
            fail(f"mutation fixture error was {completed.stderr}")
        clean = subprocess.run(
            [sys.executable, str(TOOL), "classify", "--root", str(root)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if clean.returncode != 0:
            fail(clean.stderr)
        report = json.loads(clean.stdout)
        if report["mutation"] != "NONE" or report["network"] != "NONE":
            fail("classify report claimed a mutation or network path")
        if report["profile"] != "development-code":
            fail("classify CLI profile mismatch")


def main() -> None:
    test_profiles()
    test_sensitive_action_refs()
    test_docs_missing_codeql_is_not_a_failure()
    test_empty_controls_are_deferred()
    test_missing_visibility_is_unavailable()
    test_equivalent_evidence()
    test_unknown_privileged_provenance_fails_closed()
    test_outcome_has_no_mutation_path()
    print("PASS security profile classifier and read-only audit")


if __name__ == "__main__":
    main()
