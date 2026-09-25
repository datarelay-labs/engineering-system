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
from jsonschema import Draft202012Validator

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


PROJECT_VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "schemas" / "project.schema.json").read_text(encoding="utf-8"))
)


def write_project(
    root: Path,
    *,
    maturity: str,
    production_oriented: bool | None,
) -> None:
    operations: dict[str, object] = {}
    if production_oriented is not None:
        operations["production_oriented"] = production_oriented
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


def write_origin(root: Path, slug: str) -> None:
    git = root / ".git"
    git.mkdir(parents=True, exist_ok=True)
    (git / "config").write_text(
        '[remote "origin"]\n'
        f"\turl = https://github.com/{slug}.git\n",
        encoding="utf-8",
    )


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
        write_project(empty, maturity="experimental", production_oriented=False)
        (empty / "README.md").write_text("bootstrap\n", encoding="utf-8")
        write_origin(empty, "example/app")
        report = PROFILE.build_report(empty, enabled_fixture(bootstrap="allow-no-tests"))
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


def test_credentialed_infra_remote_refs_fail_sensitive_pin() -> None:
    """infra.yml has no deploy/release/production text; credential and apply signals are enough."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_project(root, maturity="production", production_oriented=True)
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        tag_workflow = (
            "name: infra\n"
            "on: workflow_dispatch\n"
            "permissions:\n"
            "  id-token: write\n"
            "  contents: read\n"
            "jobs:\n"
            "  mutate:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: aws-actions/configure-aws-credentials@v4\n"
            "      - run: terraform apply -auto-approve\n"
        )
        write_workflow(root, "infra.yml", tag_workflow)
        report = PROFILE.build_report(root, enabled_fixture())
        if state_of(report, "sensitive_action_pin") != "REQUIRED_FAIL":
            fail("tag-pinned credentialed infra workflow was not sensitive REQUIRED_FAIL")
        if state_of(report, "ordinary_action_pin") == "RECOMMENDED_GAP":
            fail("credentialed infra remote use was downgraded to ordinary_action_pin")
        write_workflow(root, "infra.yml", tag_workflow.replace("@v4", "@main"))
        report = PROFILE.build_report(root, enabled_fixture())
        if state_of(report, "sensitive_action_pin") != "REQUIRED_FAIL":
            fail("branch-pinned credentialed infra workflow was not sensitive REQUIRED_FAIL")
        if state_of(report, "ordinary_action_pin") == "RECOMMENDED_GAP":
            fail("branch-pinned infra remote use was downgraded to ordinary_action_pin")


def test_write_permissions_and_external_secrets_fail_sensitive_pin() -> None:
    cases = {
        "packages.yml": (
            "name: packages\n"
            "on: workflow_dispatch\n"
            "jobs:\n"
            "  push:\n"
            "    runs-on: ubuntu-latest\n"
            "    permissions:\n"
            "      contents: read\n"
            "      packages: write\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
        ),
        "broad.yml": (
            "name: broad\n"
            "on: workflow_dispatch\n"
            "permissions: write-all\n"
            "jobs:\n"
            "  push:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@main\n"
        ),
        "registry.yml": (
            "name: registry\n"
            "on: workflow_dispatch\n"
            "permissions:\n"
            "  contents: read\n"
            "jobs:\n"
            "  push:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: docker/login-action@v3\n"
            "        with:\n"
            "          password: ${{ secrets.REGISTRY_PASSWORD }}\n"
        ),
        "caller.yml": (
            "name: caller\n"
            "on: workflow_dispatch\n"
            "jobs:\n"
            "  call:\n"
            "    uses: example/shared/.github/workflows/external.yml@v1\n"
            "    secrets: inherit\n"
        ),
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_project(root, maturity="production", production_oriented=True)
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        for name, body in cases.items():
            write_workflow(root, name, body)
            report = PROFILE.build_report(root, enabled_fixture())
            if state_of(report, "sensitive_action_pin") != "REQUIRED_FAIL":
                fail(f"{name} non-SHA remote use was not sensitive REQUIRED_FAIL")
            if state_of(report, "ordinary_action_pin") == "RECOMMENDED_GAP":
                fail(f"{name} remote use was downgraded to ordinary_action_pin")
            (root / ".github" / "workflows" / name).unlink()
        write_workflow(
            root,
            "check.yml",
            "name: check\n"
            "on: workflow_dispatch\n"
            "permissions:\n"
            "  contents: read\n"
            "jobs:\n"
            "  read:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "        with:\n"
            "          token: ${{ secrets.GITHUB_TOKEN }}\n",
        )
        report = PROFILE.build_report(root, enabled_fixture())
        if state_of(report, "sensitive_action_pin") == "REQUIRED_FAIL":
            fail("read permission and GITHUB_TOKEN were treated as credentialed external write")
        if state_of(report, "ordinary_action_pin") != "RECOMMENDED_GAP":
            fail("read-only tag pin was not an ordinary gap")


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


def test_schema_valid_empty_preproduct() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_project(root, maturity="experimental", production_oriented=False)
        write_origin(root, "example/app")
        loaded = yaml.safe_load((root / ".engineering" / "project.yaml").read_text(encoding="utf-8"))
        errors = sorted(PROJECT_VALIDATOR.iter_errors(loaded), key=lambda item: list(item.path))
        if errors:
            fail(errors[0].message)
        if "pre_product" in json.dumps(loaded):
            fail("schema-valid fixture used an undefined pre_product field")
        inferred = PROFILE.build_report(root, enabled_fixture())
        if inferred["profile"] == "empty-preproduct":
            fail("schema-valid repo was inferred empty without canonical bootstrap evidence")
        undefined = dict(loaded)
        undefined["operations"] = dict(undefined["operations"])
        undefined["operations"]["pre_product"] = True
        (root / ".engineering" / "project.yaml").write_text(yaml.safe_dump(undefined), encoding="utf-8")
        ignored = PROFILE.build_report(root, enabled_fixture())
        if ignored["profile"] == "empty-preproduct":
            fail("undefined operations.pre_product granted empty-preproduct")
        (root / ".engineering" / "project.yaml").write_text(yaml.safe_dump(loaded), encoding="utf-8")
        report = PROFILE.build_report(root, enabled_fixture(bootstrap="allow-no-tests", codeql_default_setup="configured"))
        if report["profile"] != "empty-preproduct":
            fail(f"schema-valid allow-no-tests repo was {report['profile']}: {report['reason']}")
        states = {item["state"] for item in report["controls"]}
        if states != {"DEFERRED"}:
            fail(f"empty/preproduct fabricated states {states}")


def test_adopted_empty_compliance_workflow_stays_preproduct() -> None:
    compliance = (
        "name: Engineering System\n"
        "on:\n"
        "  pull_request:\n"
        "permissions:\n"
        "  contents: read\n"
        "jobs:\n"
        "  adoption-compliance:\n"
        f"    uses: datarelay-labs/engineering-system/.github/workflows/adoption-compliance.yml@{SHA}\n"
        "  enforcement-reconcile:\n"
        f"    uses: datarelay-labs/engineering-system/.github/workflows/enforcement-check.yml@{SHA}\n"
        "  affected-tests:\n"
        f"    uses: datarelay-labs/engineering-system/.github/workflows/affected-tests.yml@{SHA}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_project(root, maturity="experimental", production_oriented=False)
        write_origin(root, "example/app")
        loaded = yaml.safe_load((root / ".engineering" / "project.yaml").read_text(encoding="utf-8"))
        errors = sorted(PROJECT_VALIDATOR.iter_errors(loaded), key=lambda item: list(item.path))
        if errors:
            fail(errors[0].message)
        write_workflow(root, "engineering-system.yml", compliance)
        report = PROFILE.build_report(root, enabled_fixture(bootstrap="allow-no-tests"))
        if report["profile"] != "empty-preproduct":
            fail(f"adopted empty compliance workflow was {report['profile']}: {report['reason']}")
        states = {item["state"] for item in report["controls"]}
        if states != {"DEFERRED"}:
            fail(f"adopted empty repo fabricated states {states}")
        write_workflow(
            root,
            "release.yml",
            "name: ship\non: push\njobs:\n  ship:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses: actions/checkout@v4\n",
        )
        blocked = PROFILE.build_report(root, enabled_fixture(bootstrap="allow-no-tests"))
        if blocked["profile"] == "empty-preproduct":
            fail("deploy/release workflow still classified empty-preproduct")
        if "conflicts" not in blocked["reason"]:
            fail(f"release workflow did not conflict with bootstrap: {blocked['reason']}")


def test_empty_controls_are_deferred() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_project(root, maturity="experimental", production_oriented=False)
        write_origin(root, "example/app")
        report = PROFILE.build_report(root, enabled_fixture(bootstrap="allow-no-tests", codeql_default_setup="configured"))
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
        "scope": "repository",
        "approval_state": "approved",
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_project(root, maturity="production", production_oriented=True)
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        write_origin(root, "example/app")
        (root / "README.md").write_text(
            "EQUIVALENT_EXTERNAL provider=example-sast control_id=codeql_or_sast\n",
            encoding="utf-8",
        )
        prose = PROFILE.build_report(root, enabled_fixture(codeql_default_setup="not-configured"))
        if state_of(prose, "codeql_or_sast") != "REQUIRED_FAIL":
            fail("repository prose granted EQUIVALENT_EXTERNAL")
        accepted = PROFILE.build_report(
            root,
            enabled_fixture(
                codeql_default_setup="not-configured",
                scope="repository",
                equivalent_external=[valid],
            ),
        )
        if state_of(accepted, "codeql_or_sast") != "EQUIVALENT_EXTERNAL":
            fail("valid equivalent evidence was rejected")
        missing_scope = dict(valid)
        del missing_scope["scope"]
        unbound = PROFILE.build_report(
            root,
            enabled_fixture(
                codeql_default_setup="not-configured",
                scope="repository",
                equivalent_external=[missing_scope],
            ),
        )
        if state_of(unbound, "codeql_or_sast") == "EQUIVALENT_EXTERNAL":
            fail("evidence without scope was accepted")
        mismatched = dict(valid)
        mismatched["scope"] = "actions"
        wrong_scope = PROFILE.build_report(
            root,
            enabled_fixture(
                codeql_default_setup="not-configured",
                scope="repository",
                equivalent_external=[mismatched],
            ),
        )
        if state_of(wrong_scope, "codeql_or_sast") == "EQUIVALENT_EXTERNAL":
            fail("evidence for a different scope was accepted")
        invalid = dict(valid)
        invalid["approval_state"] = "pending"
        rejected = PROFILE.build_report(
            root,
            enabled_fixture(
                codeql_default_setup="not-configured",
                scope="repository",
                equivalent_external=[invalid],
            ),
        )
        if state_of(rejected, "codeql_or_sast") == "EQUIVALENT_EXTERNAL":
            fail("invalid equivalent evidence was accepted")


def test_foreign_github_fixture_cannot_pass() -> None:
    github_controls = (
        "repository_visibility",
        "secret_scanning",
        "push_protection",
        "dependabot_security_updates",
        "codeql_or_sast",
    )
    pass_states = {"REQUIRED_PASS", "RECOMMENDED_PASS", "EQUIVALENT_EXTERNAL"}
    foreign_evidence = {
        "control_id": "codeql_or_sast",
        "provider": "example-sast",
        "immutable_id": "scanner-1.2.3",
        "evidence_ref": "evidence/sast/2026-09-25",
        "timestamp": "2026-09-25T00:00:00Z",
        "repository": "totally-different/repo",
        "scope": "repository",
        "approval_state": "approved",
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "target-repo"
        root.mkdir()
        write_project(root, maturity="production", production_oriented=True)
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        foreign = enabled_fixture(
            repository="totally-different/repo",
            scope="repository",
            equivalent_external=[foreign_evidence],
        )
        unbound = PROFILE.build_report(root, foreign)
        for control_id in github_controls:
            if state_of(unbound, control_id) in pass_states:
                fail(f"unbound fixture yielded {state_of(unbound, control_id)} for {control_id}")
        write_origin(root, "example/app")
        mismatched = PROFILE.build_report(root, foreign)
        for control_id in github_controls:
            if state_of(mismatched, control_id) in pass_states:
                fail(f"foreign fixture yielded {state_of(mismatched, control_id)} for {control_id}")
        matched = PROFILE.build_report(root, enabled_fixture())
        for control_id in github_controls:
            if state_of(matched, control_id) != "REQUIRED_PASS":
                fail(f"bound fixture did not pass {control_id}: {state_of(matched, control_id)}")
        bound_evidence = dict(foreign_evidence)
        bound_evidence["repository"] = "example/app"
        accepted = PROFILE.build_report(
            root,
            enabled_fixture(
                codeql_default_setup="not-configured",
                scope="repository",
                equivalent_external=[bound_evidence],
            ),
        )
        if state_of(accepted, "codeql_or_sast") != "EQUIVALENT_EXTERNAL":
            fail("bound equivalent evidence was rejected")


def test_worktree_commondir_binds_repository() -> None:
    github_controls = (
        "repository_visibility",
        "secret_scanning",
        "push_protection",
        "dependabot_security_updates",
        "codeql_or_sast",
    )
    pass_states = {"REQUIRED_PASS", "RECOMMENDED_PASS", "EQUIVALENT_EXTERNAL"}
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        common = base / "repo.git"
        admin = common / "worktrees" / "p2-coordinator-planner"
        admin.mkdir(parents=True)
        (common / "config").write_text(
            '[remote "origin"]\n'
            "\turl = https://github.com/datarelay-labs/engineering-system.git\n",
            encoding="utf-8",
        )
        (admin / "commondir").write_text("../..\n", encoding="utf-8")
        root = base / "target-repo"
        root.mkdir()
        write_project(root, maturity="production", production_oriented=True)
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        (root / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")
        if PROFILE.local_repository_identity(root) != "datarelay-labs/engineering-system":
            fail(f"worktree identity was {PROFILE.local_repository_identity(root)!r}")
        if not PROFILE.read_git_config(root):
            fail("worktree common config was not read")
        foreign = PROFILE.build_report(root, enabled_fixture(repository="totally-different/repo"))
        for control_id in github_controls:
            if state_of(foreign, control_id) in pass_states:
                fail(f"worktree foreign fixture yielded {state_of(foreign, control_id)} for {control_id}")
        matched = PROFILE.build_report(
            root,
            enabled_fixture(repository="datarelay-labs/engineering-system"),
        )
        for control_id in github_controls:
            if state_of(matched, control_id) != "REQUIRED_PASS":
                fail(f"worktree bound fixture did not pass {control_id}: {state_of(matched, control_id)}")
        outside = base / "outside"
        outside.mkdir()
        (outside / "config").write_text(
            '[remote "origin"]\n'
            "\turl = https://github.com/datarelay-labs/engineering-system.git\n",
            encoding="utf-8",
        )
        (admin / "commondir").write_text("../../../outside\n", encoding="utf-8")
        if PROFILE.local_repository_identity(root) is not None:
            fail("out-of-bound commondir resolved an identity")
        escaped = PROFILE.build_report(
            root,
            enabled_fixture(repository="datarelay-labs/engineering-system"),
        )
        for control_id in github_controls:
            if state_of(escaped, control_id) in pass_states:
                fail(f"out-of-bound commondir yielded {state_of(escaped, control_id)} for {control_id}")


def test_foreign_fixture_cannot_grant_privileged_or_preproduct() -> None:
    known_tool = {
        "id": "mcp.deploy",
        "privileged": True,
        "provenance": "approved",
        "provider": "example",
        "immutable_id": "tool-1",
    }
    pass_states = {"REQUIRED_PASS", "RECOMMENDED_PASS", "EQUIVALENT_EXTERNAL"}
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        code = base / "target-repo"
        code.mkdir()
        write_project(code, maturity="production", production_oriented=True)
        (code / "src").mkdir()
        (code / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        write_origin(code, "example/app")
        foreign_tools = PROFILE.build_report(
            code,
            enabled_fixture(repository="other/repo", privileged_tools=[known_tool]),
        )
        if foreign_tools["profile"] != "production-code":
            fail(f"foreign privileged fixture changed profile to {foreign_tools['profile']}")
        if state_of(foreign_tools, "privileged_tool_provenance") in pass_states:
            fail("foreign privileged fixture granted provenance PASS")
        if state_of(foreign_tools, "privileged_tool_provenance") != "UNAVAILABLE":
            fail(
                "foreign privileged fixture was "
                f"{state_of(foreign_tools, 'privileged_tool_provenance')}"
            )
        bound_tools = PROFILE.build_report(code, enabled_fixture(privileged_tools=[known_tool]))
        if state_of(bound_tools, "privileged_tool_provenance") != "REQUIRED_PASS":
            fail("bound privileged provenance did not pass")

        empty = base / "empty-repo"
        empty.mkdir()
        write_project(empty, maturity="experimental", production_oriented=False)
        write_origin(empty, "example/app")
        foreign_bootstrap = PROFILE.build_report(
            empty,
            enabled_fixture(repository="other/repo", bootstrap="allow-no-tests"),
        )
        if foreign_bootstrap["profile"] == "empty-preproduct":
            fail("foreign bootstrap classified empty-preproduct")
        bound_bootstrap = PROFILE.build_report(empty, enabled_fixture(bootstrap="allow-no-tests"))
        if bound_bootstrap["profile"] != "empty-preproduct":
            fail(f"bound bootstrap was {bound_bootstrap['profile']}: {bound_bootstrap['reason']}")

        unbound = base / "unbound-repo"
        unbound.mkdir()
        write_project(unbound, maturity="production", production_oriented=True)
        (unbound / "src").mkdir()
        (unbound / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        unavailable_tools = PROFILE.build_report(unbound, enabled_fixture(privileged_tools=[known_tool]))
        if state_of(unavailable_tools, "privileged_tool_provenance") in pass_states:
            fail("unavailable binding granted privileged PASS")
        empty_unbound = base / "empty-unbound"
        empty_unbound.mkdir()
        write_project(empty_unbound, maturity="experimental", production_oriented=False)
        unavailable_bootstrap = PROFILE.build_report(
            empty_unbound,
            enabled_fixture(bootstrap="allow-no-tests"),
        )
        if unavailable_bootstrap["profile"] == "empty-preproduct":
            fail("unavailable binding classified empty-preproduct")


def test_forged_origin_key_and_symlink_git_do_not_bind() -> None:
    github_controls = (
        "repository_visibility",
        "secret_scanning",
        "push_protection",
        "dependabot_security_updates",
        "codeql_or_sast",
    )
    pass_states = {"REQUIRED_PASS", "RECOMMENDED_PASS", "EQUIVALENT_EXTERNAL"}
    known_tool = {
        "id": "mcp.deploy",
        "privileged": True,
        "provenance": "approved",
        "provider": "example",
        "immutable_id": "tool-1",
    }
    forged_config = (
        '[remote "origin"]\n'
        "\turlBogus = https://github.com/evil/forged.git\n"
    )

    def production_root(parent: Path, name: str) -> Path:
        root = parent / name
        root.mkdir()
        write_project(root, maturity="production", production_oriented=True)
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        return root

    def assert_unbound(root: Path) -> None:
        if PROFILE.local_repository_identity(root) is not None:
            fail(f"unbound identity was {PROFILE.local_repository_identity(root)!r}")
        report = PROFILE.build_report(
            root,
            enabled_fixture(repository="evil/forged", privileged_tools=[known_tool]),
        )
        for control_id in github_controls:
            if state_of(report, control_id) in pass_states:
                fail(f"forged binding yielded {state_of(report, control_id)} for {control_id}")
        if state_of(report, "privileged_tool_provenance") in pass_states:
            fail("forged binding granted privileged provenance PASS")

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        bogus = production_root(base, "bogus-key")
        git = bogus / ".git"
        git.mkdir()
        (git / "config").write_text(forged_config, encoding="utf-8")
        assert_unbound(bogus)

        exact = production_root(base, "exact-url")
        exact_git = exact / ".git"
        exact_git.mkdir()
        (exact_git / "config").write_text(
            forged_config + "\tURL = https://github.com/example/app.git\n",
            encoding="utf-8",
        )
        if PROFILE.local_repository_identity(exact) != "example/app":
            fail(f"exact url key was {PROFILE.local_repository_identity(exact)!r}")
        forged = PROFILE.build_report(exact, enabled_fixture(repository="evil/forged"))
        for control_id in github_controls:
            if state_of(forged, control_id) in pass_states:
                fail(f"urlBogus yielded {state_of(forged, control_id)} for {control_id}")
        matched = PROFILE.build_report(exact, enabled_fixture())
        for control_id in github_controls:
            if state_of(matched, control_id) != "REQUIRED_PASS":
                fail(f"exact url key did not pass {control_id}: {state_of(matched, control_id)}")

        outside = base / "outside.git"
        outside.mkdir()
        (outside / "config").write_text(
            '[remote "origin"]\n'
            "\turl = https://github.com/evil/forged.git\n",
            encoding="utf-8",
        )
        linked_dir = production_root(base, "linked-dir")
        (linked_dir / ".git").symlink_to(outside, target_is_directory=True)
        if PROFILE.read_git_config(linked_dir):
            fail("symlink .git directory was read")
        assert_unbound(linked_dir)

        admin = base / "admin.git"
        admin.mkdir()
        (admin / "config").write_text(
            '[remote "origin"]\n'
            "\turl = https://github.com/evil/forged.git\n",
            encoding="utf-8",
        )
        pointer = base / "gitdir-file"
        pointer.write_text(f"gitdir: {admin}\n", encoding="utf-8")
        linked_file = production_root(base, "linked-file")
        (linked_file / ".git").symlink_to(pointer)
        if PROFILE.read_git_config(linked_file):
            fail("symlink .git file was read")
        assert_unbound(linked_file)

        linked_config = production_root(base, "linked-config")
        config_dir = linked_config / ".git"
        config_dir.mkdir()
        forged_file = base / "forged-config"
        forged_file.write_text(
            '[remote "origin"]\n'
            "\turl = https://github.com/evil/forged.git\n",
            encoding="utf-8",
        )
        (config_dir / "config").symlink_to(forged_file)
        if PROFILE.read_git_config(linked_config):
            fail("symlink config path was read")
        assert_unbound(linked_config)


def test_authoritative_symlinks_cannot_grant_pass() -> None:
    github_controls = (
        "repository_visibility",
        "secret_scanning",
        "push_protection",
        "dependabot_security_updates",
        "codeql_or_sast",
    )
    pass_states = {"REQUIRED_PASS", "RECOMMENDED_PASS", "EQUIVALENT_EXTERNAL"}
    pinned_release = (
        "name: release\n"
        "on: workflow_dispatch\n"
        "jobs:\n"
        "  publish:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        f"      - uses: actions/checkout@{SHA}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        outside = base / "outside"
        outside.mkdir()
        (outside / "release.yml").write_text(pinned_release, encoding="utf-8")
        (outside / "main.py").write_text("print('external')\n", encoding="utf-8")
        write_project(outside, maturity="production", production_oriented=True)

        workflow_root = base / "workflow-link"
        workflow_root.mkdir()
        write_project(workflow_root, maturity="production", production_oriented=True)
        (workflow_root / "src").mkdir()
        (workflow_root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        workflows = workflow_root / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "release.yml").symlink_to(outside / "release.yml")
        workflow_report = PROFILE.build_report(workflow_root, enabled_fixture())
        if workflow_report["profile"] != "production-code":
            fail(f"real project profile changed to {workflow_report['profile']}")
        if state_of(workflow_report, "sensitive_action_pin") in pass_states:
            fail("symlink workflow granted sensitive_action_pin PASS")
        if state_of(workflow_report, "sensitive_action_pin") != "REQUIRED_FAIL":
            fail(
                "symlink workflow was "
                f"{state_of(workflow_report, 'sensitive_action_pin')}"
            )

        linked_workflows = base / "workflow-dir-link"
        linked_workflows.mkdir()
        write_project(linked_workflows, maturity="production", production_oriented=True)
        (linked_workflows / "src").mkdir()
        (linked_workflows / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        external_workflows = outside / "workflows"
        external_workflows.mkdir()
        (external_workflows / "release.yml").write_text(pinned_release, encoding="utf-8")
        (linked_workflows / ".github").mkdir()
        (linked_workflows / ".github" / "workflows").symlink_to(external_workflows, target_is_directory=True)
        directory_report = PROFILE.build_report(linked_workflows, enabled_fixture())
        if state_of(directory_report, "sensitive_action_pin") in pass_states:
            fail("symlink workflow directory granted sensitive_action_pin PASS")
        if state_of(directory_report, "sensitive_action_pin") != "REQUIRED_FAIL":
            fail(
                "symlink workflow directory was "
                f"{state_of(directory_report, 'sensitive_action_pin')}"
            )

        profile_root = base / "profile-link"
        profile_root.mkdir()
        (profile_root / "src").mkdir()
        (profile_root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        (profile_root / ".engineering").mkdir()
        (profile_root / ".engineering" / "project.yaml").symlink_to(
            outside / ".engineering" / "project.yaml"
        )
        write_origin(profile_root, "example/app")
        profile_report = PROFILE.build_report(profile_root, enabled_fixture())
        if profile_report["profile"] != "NEEDS_INPUT":
            fail(f"symlink project profile was {profile_report['profile']}")
        for control_id in (*github_controls, "sensitive_action_pin"):
            if state_of(profile_report, control_id) in pass_states:
                fail(f"symlink project profile granted {state_of(profile_report, control_id)} for {control_id}")

        ordinary = base / "ordinary-link"
        ordinary.mkdir()
        write_project(ordinary, maturity="development", production_oriented=False)
        (ordinary / "src").mkdir()
        (ordinary / "src" / "main.py").symlink_to(outside / "main.py")
        ordinary_report = PROFILE.build_report(ordinary, None)
        if ordinary_report["profile"] != "development-code":
            fail(f"ordinary source symlink was {ordinary_report['profile']}: {ordinary_report['reason']}")


def test_unknown_privileged_provenance_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_project(root, maturity="production", production_oriented=True)
        write_origin(root, "example/app")
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
        malformed_cases = (
            ["mcp.deploy"],
            "unknown",
            [{"id": "mcp.deploy"}],
            [{"id": "mcp.deploy", "privileged": True, "note": "unbounded"}],
        )
        for case in malformed_cases:
            report = PROFILE.build_report(root, enabled_fixture(privileged_tools=case))
            if state_of(report, "privileged_tool_provenance") != "REQUIRED_FAIL":
                fail(f"malformed privileged_tools {case!r} was {state_of(report, 'privileged_tool_provenance')}")


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
    test_credentialed_infra_remote_refs_fail_sensitive_pin()
    test_write_permissions_and_external_secrets_fail_sensitive_pin()
    test_docs_missing_codeql_is_not_a_failure()
    test_schema_valid_empty_preproduct()
    test_adopted_empty_compliance_workflow_stays_preproduct()
    test_empty_controls_are_deferred()
    test_missing_visibility_is_unavailable()
    test_equivalent_evidence()
    test_foreign_github_fixture_cannot_pass()
    test_foreign_fixture_cannot_grant_privileged_or_preproduct()
    test_worktree_commondir_binds_repository()
    test_forged_origin_key_and_symlink_git_do_not_bind()
    test_authoritative_symlinks_cannot_grant_pass()
    test_unknown_privileged_provenance_fails_closed()
    test_outcome_has_no_mutation_path()
    print("PASS security profile classifier and read-only audit")


if __name__ == "__main__":
    main()
