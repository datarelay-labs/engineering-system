#!/usr/bin/env python3
"""Regressions for the network-free native CI policy auditor."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "ci_policy_audit.py"
ENGINEERING_TEST = ROOT / "tools" / "engineering-test.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"FAIL cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUDIT = load_module(TOOL, "ci_policy_audit")
SELECTOR = load_module(ENGINEERING_TEST, "engineering_test_cost")


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def write_repo(
    root: Path,
    *,
    workflows: dict[str, str],
    mapped: list[str],
    scenarios: list[dict],
) -> None:
    engineering = root / ".engineering"
    engineering.mkdir(parents=True, exist_ok=True)
    project = {
        "engineering_system": {
            "version": "1.6.5",
            "native_ci_workflows": mapped,
        }
    }
    (engineering / "project.yaml").write_text(yaml.safe_dump(project), encoding="utf-8")
    (engineering / "tests.yaml").write_text(
        yaml.safe_dump({"version": 1, "scenarios": scenarios}),
        encoding="utf-8",
    )
    workflow_dir = root / ".github" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    for name, body in workflows.items():
        (workflow_dir / name).write_text(body, encoding="utf-8")


def run_audit(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), "--root", str(root)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def assert_pass(root: Path, label: str) -> None:
    result = run_audit(root)
    if result.returncode != 0:
        fail(f"{label} expected PASS, got {result.returncode}: {result.stdout} {result.stderr}")
    if result.stdout.splitlines() != ["VIOLATIONS=0"]:
        fail(f"{label} expected VIOLATIONS=0, got {result.stdout!r}")


def test_drlink_shape_reports_five_violations() -> None:
    overlap = "name: native\non:\n  push:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
    lint = (
        "name: native\n"
        "on:\n"
        "  push:\n"
        "  pull_request:\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: './tests/run-all.sh'\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_repo(
            root,
            workflows={
                "lint.yml": lint,
                "macos-client.yml": overlap,
                "windows-client.yml": overlap,
                "unmapped.yml": overlap,
            },
            mapped=[
                ".github/workflows/lint.yml",
                ".github/workflows/macos-client.yml",
                ".github/workflows/windows-client.yml",
            ],
            scenarios=[
                {
                    "id": "ADOPTED-TEST-001",
                    "level": "integration",
                    "command": "bash tests/run-all.sh",
                    "triggers": ["affected"],
                    "release_gate": True,
                }
            ],
        )
        loaded = yaml.safe_load((root / ".github" / "workflows" / "lint.yml").read_text(encoding="utf-8"))
        if True not in loaded:
            fail("DRLink fixture did not parse bare on as boolean True")
        result = run_audit(root)
        expected = [
            "VIOLATIONS=5",
            "DUPLICATE_NATIVE_CI .github/workflows/lint.yml",
            "DUPLICATE_NATIVE_CI .github/workflows/macos-client.yml",
            "DUPLICATE_NATIVE_CI .github/workflows/windows-client.yml",
            'EXPENSIVE_DEFAULT_GATE ADOPTED-TEST-001 cost=expensive triggers=[affected] command="bash tests/run-all.sh"',
            "EXPENSIVE_PR_WORKFLOW .github/workflows/lint.yml scenario=ADOPTED-TEST-001 "
            'cost=expensive command="bash tests/run-all.sh" triggers=[affected]',
        ]
        if result.returncode == 0:
            fail(f"DRLink shape expected nonzero, got {result.stdout!r}")
        if result.stdout.splitlines() != expected:
            fail(f"DRLink shape mismatch:\n{result.stdout}")


def test_pr_only_cheap_passes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_repo(
            root,
            workflows={
                "unit.yml": "name: unit\non:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
            },
            mapped=[".github/workflows/unit.yml"],
            scenarios=[
                {
                    "id": "UNIT-001",
                    "level": "unit",
                    "command": "python3 -m unittest",
                    "triggers": ["affected", "pr"],
                    "release_gate": True,
                }
            ],
        )
        assert_pass(root, "PR-only cheap")


def test_tags_only_push_plus_pr_passes() -> None:
    body = (
        "name: release\n"
        "on:\n"
        "  push:\n"
        "    tags:\n"
        "      - v*\n"
        "  pull_request:\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_repo(
            root,
            workflows={"release.yml": body},
            mapped=[".github/workflows/release.yml"],
            scenarios=[
                {
                    "id": "REL-001",
                    "level": "e2e",
                    "cost": "expensive",
                    "command": "bash tests/run-all.sh",
                    "triggers": ["release"],
                    "release_gate": True,
                }
            ],
        )
        assert_pass(root, "tags-only push + PR")


def test_workflow_dispatch_and_schedule_pass() -> None:
    body = (
        "name: nightly\n"
        "on:\n"
        "  workflow_dispatch:\n"
        "  schedule:\n"
        "    - cron: '0 0 * * *'\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_repo(
            root,
            workflows={"nightly.yml": body},
            mapped=[".github/workflows/nightly.yml"],
            scenarios=[
                {
                    "id": "NIGHTLY-001",
                    "level": "performance",
                    "command": "bash tests/run-all.sh",
                    "triggers": ["preflight"],
                    "release_gate": True,
                }
            ],
        )
        assert_pass(root, "workflow_dispatch/schedule")


def test_effective_cost_matches_engineering_test() -> None:
    samples = [
        {"level": "static"},
        {"level": "unit"},
        {"level": "component"},
        {"level": "feature"},
        {"level": "integration"},
        {"level": "ux"},
        {"level": "lifecycle"},
        {"level": "performance"},
        {"level": "e2e"},
        {"level": "unknown"},
        {},
        {"level": "static", "cost": "expensive"},
        {"level": "e2e", "cost": "cheap"},
        {"level": "integration", "cost": "medium"},
        {"cost": "  cheap  "},
    ]
    for scenario in samples:
        expected = SELECTOR.scenario_cost(scenario)
        observed = AUDIT.scenario_cost(scenario)
        if observed != expected:
            fail(f"cost mismatch for {scenario}: auditor={observed} selector={expected}")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_repo(
            root,
            workflows={
                "unit.yml": (
                    "name: unit\n"
                    "on:\n"
                    "  pull_request:\n"
                    "jobs:\n"
                    "  test:\n"
                    "    runs-on: ubuntu-latest\n"
                    "    steps:\n"
                    "      - run: ./tests/run-all.sh\n"
                    "      - run: python3 -m unittest\n"
                    "      - run: python3 -m unittest component\n"
                ),
            },
            mapped=[".github/workflows/unit.yml"],
            scenarios=[
                {
                    "id": "OVERRIDE-EXPENSIVE",
                    "level": "static",
                    "cost": "expensive",
                    "command": "bash tests/run-all.sh",
                    "triggers": ["release"],
                    "release_gate": True,
                },
                {
                    "id": "OVERRIDE-CHEAP",
                    "level": "e2e",
                    "cost": "cheap",
                    "command": "python3 -m unittest",
                    "triggers": ["release"],
                    "release_gate": True,
                },
                {
                    "id": "LEVEL-MEDIUM",
                    "level": "feature",
                    "command": "python3 -m unittest component",
                    "triggers": ["release"],
                    "release_gate": True,
                },
            ],
        )
        result = run_audit(root)
        lines = result.stdout.splitlines()
        if result.returncode == 0 or lines != [
            "VIOLATIONS=1",
            "EXPENSIVE_PR_WORKFLOW .github/workflows/unit.yml scenario=OVERRIDE-EXPENSIVE "
            'cost=expensive command="bash tests/run-all.sh" triggers=[release]',
        ]:
            fail(f"explicit cost override mismatch:\n{result.stdout}")


def test_expensive_affected_not_invoked_reports_default_gate() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_repo(
            root,
            workflows={
                "unit.yml": (
                    "name: unit\n"
                    "on:\n"
                    "  pull_request:\n"
                    "jobs:\n"
                    "  test:\n"
                    "    runs-on: ubuntu-latest\n"
                    "    steps:\n"
                    "      - run: python3 -m unittest\n"
                ),
                "release.yml": (
                    "name: release\n"
                    "on:\n"
                    "  push:\n"
                    "    branches:\n"
                    "      - main\n"
                    "jobs:\n"
                    "  full:\n"
                    "    runs-on: ubuntu-latest\n"
                    "    steps:\n"
                    "      - run: ./tests/run-all.sh\n"
                ),
            },
            mapped=[
                ".github/workflows/unit.yml",
                ".github/workflows/release.yml",
            ],
            scenarios=[
                {
                    "id": "UX-001",
                    "level": "ux",
                    "command": "bash tests/run-all.sh",
                    "triggers": ["affected"],
                    "release_gate": True,
                }
            ],
        )
        result = run_audit(root)
        expected = [
            "VIOLATIONS=1",
            'EXPENSIVE_DEFAULT_GATE UX-001 cost=expensive triggers=[affected] command="bash tests/run-all.sh"',
        ]
        if result.returncode == 0 or result.stdout.splitlines() != expected:
            fail(f"expensive affected not invoked mismatch:\n{result.stdout}")


def test_bash_vs_dot_slash_unconditional_pr_fails() -> None:
    if AUDIT.normalize_direct_invocation("bash tests/run-all.sh") != ("tests/run-all.sh", ()):
        fail("bash script normalization mismatch")
    if AUDIT.normalize_direct_invocation("./tests/run-all.sh") != ("tests/run-all.sh", ()):
        fail("./ script normalization mismatch")
    if AUDIT.normalize_direct_invocation("sh ./tests/run-all.sh --suite ux") != (
        "tests/run-all.sh",
        ("--suite", "ux"),
    ):
        fail("sh ./ args normalization mismatch")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_repo(
            root,
            workflows={
                "lint.yml": (
                    "name: lint\n"
                    "on:\n"
                    "  pull_request:\n"
                    "jobs:\n"
                    "  test:\n"
                    "    runs-on: ubuntu-latest\n"
                    "    steps:\n"
                    "      - run: ./tests/run-all.sh\n"
                ),
            },
            mapped=[".github/workflows/lint.yml"],
            scenarios=[
                {
                    "id": "ADOPTED-TEST-001",
                    "level": "integration",
                    "command": "bash tests/run-all.sh",
                    "triggers": ["release"],
                    "release_gate": True,
                }
            ],
        )
        result = run_audit(root)
        expected = [
            "VIOLATIONS=1",
            "EXPENSIVE_PR_WORKFLOW .github/workflows/lint.yml scenario=ADOPTED-TEST-001 "
            'cost=expensive command="bash tests/run-all.sh" triggers=[release]',
        ]
        if result.returncode == 0 or result.stdout.splitlines() != expected:
            fail(f"bash vs ./ unconditional PR mismatch:\n{result.stdout}")


def test_conditional_job_and_step_pass() -> None:
    job_if = (
        "name: lint\n"
        "on:\n"
        "  pull_request:\n"
        "jobs:\n"
        "  test:\n"
        "    if: github.event_name == 'pull_request'\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: ./tests/run-all.sh\n"
    )
    step_if = (
        "name: lint\n"
        "on:\n"
        "  pull_request:\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - name: suite\n"
        "        if: success()\n"
        "        run: ./tests/run-all.sh\n"
    )
    scenario = {
        "id": "ADOPTED-TEST-001",
        "level": "integration",
        "command": "bash tests/run-all.sh",
        "triggers": ["release"],
        "release_gate": True,
    }
    for label, body in (("conditional job", job_if), ("conditional step", step_if)):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_repo(
                root,
                workflows={"lint.yml": body},
                mapped=[".github/workflows/lint.yml"],
                scenarios=[scenario],
            )
            assert_pass(root, label)


def test_cheap_pr_command_passes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_repo(
            root,
            workflows={
                "unit.yml": (
                    "name: unit\n"
                    "on:\n"
                    "  pull_request:\n"
                    "jobs:\n"
                    "  test:\n"
                    "    runs-on: ubuntu-latest\n"
                    "    steps:\n"
                    "      - run: python3 -m unittest\n"
                ),
            },
            mapped=[".github/workflows/unit.yml"],
            scenarios=[
                {
                    "id": "UNIT-001",
                    "level": "unit",
                    "command": "python3 -m unittest",
                    "triggers": ["pr"],
                    "release_gate": True,
                }
            ],
        )
        assert_pass(root, "cheap PR command")


def test_compound_shell_and_substring_pass() -> None:
    rejected = [
        "./tests/run-all.sh | tee log",
        "./tests/run-all.sh && echo ok",
        "./tests/run-all.sh > log",
        "echo ./tests/run-all.sh",
        "bash -lc ./tests/run-all.sh",
    ]
    for command in rejected:
        if AUDIT.normalize_direct_invocation(command) == ("tests/run-all.sh", ()):
            fail(f"compound or fuzzy command correlated: {command}")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_repo(
            root,
            workflows={
                "lint.yml": (
                    "name: lint\n"
                    "on:\n"
                    "  pull_request:\n"
                    "jobs:\n"
                    "  test:\n"
                    "    runs-on: ubuntu-latest\n"
                    "    steps:\n"
                    "      - run: ./tests/run-all.sh | tee log\n"
                    "      - run: echo ./tests/run-all.sh\n"
                    "      - run: |\n"
                    "          echo start\n"
                    "          ./tests/run-all.sh && echo done\n"
                    "          ./tests/run-all.sh > log\n"
                ),
            },
            mapped=[".github/workflows/lint.yml"],
            scenarios=[
                {
                    "id": "ADOPTED-TEST-001",
                    "level": "integration",
                    "command": "bash tests/run-all.sh",
                    "triggers": ["release"],
                    "release_gate": True,
                }
            ],
        )
        assert_pass(root, "compound shell and substring")


def test_multiline_standalone_direct_script_passes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_repo(
            root,
            workflows={
                "lint.yml": (
                    "name: lint\n"
                    "on:\n"
                    "  pull_request:\n"
                    "jobs:\n"
                    "  test:\n"
                    "    runs-on: ubuntu-latest\n"
                    "    steps:\n"
                    "      - run: |\n"
                    "          set -euo pipefail\n"
                    "          ./tests/run-all.sh\n"
                ),
            },
            mapped=[".github/workflows/lint.yml"],
            scenarios=[
                {
                    "id": "ADOPTED-TEST-001",
                    "level": "integration",
                    "command": "bash tests/run-all.sh",
                    "triggers": ["release"],
                    "release_gate": True,
                }
            ],
        )
        assert_pass(root, "multiline standalone direct script")


def test_multiline_shell_if_passes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_repo(
            root,
            workflows={
                "lint.yml": (
                    "name: lint\n"
                    "on:\n"
                    "  pull_request:\n"
                    "jobs:\n"
                    "  test:\n"
                    "    runs-on: ubuntu-latest\n"
                    "    steps:\n"
                    "      - run: |\n"
                    "          if [ -x ./tests/run-all.sh ]; then\n"
                    "            ./tests/run-all.sh\n"
                    "          fi\n"
                ),
            },
            mapped=[".github/workflows/lint.yml"],
            scenarios=[
                {
                    "id": "ADOPTED-TEST-001",
                    "level": "integration",
                    "command": "bash tests/run-all.sh",
                    "triggers": ["release"],
                    "release_gate": True,
                }
            ],
        )
        assert_pass(root, "multiline shell if")


def test_script_args_must_match() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_repo(
            root,
            workflows={
                "lint.yml": (
                    "name: lint\n"
                    "on:\n"
                    "  pull_request:\n"
                    "jobs:\n"
                    "  test:\n"
                    "    runs-on: ubuntu-latest\n"
                    "    steps:\n"
                    "      - run: ./tests/run-all.sh --suite ux\n"
                ),
            },
            mapped=[".github/workflows/lint.yml"],
            scenarios=[
                {
                    "id": "ADOPTED-TEST-001",
                    "level": "integration",
                    "command": "bash tests/run-all.sh",
                    "triggers": ["release"],
                    "release_gate": True,
                }
            ],
        )
        assert_pass(root, "script args differ")


def test_adoption_compliance_calls_runtime_auditor() -> None:
    workflow_path = ROOT / ".github" / "workflows" / "adoption-compliance.yml"
    document = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    steps = document["jobs"]["compliance"]["steps"]
    checkout_pin = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
    if steps[0].get("uses") != checkout_pin:
        fail(f"caller checkout changed: {steps[0].get('uses')!r}")
    if "actions/setup-python@" not in str(steps[1].get("uses") or ""):
        fail("setup-python step moved")
    if steps[2].get("run") != "python -m pip install --disable-pip-version-check PyYAML==6.0.2":
        fail("PyYAML install step changed")
    if steps[3].get("run") != "test ! -e .engineering-system-runtime":
        fail(f"runtime path guard missing: {steps[3].get('run')!r}")
    runtime_checkout = steps[4]
    runtime_with = runtime_checkout.get("with") or {}
    if runtime_checkout.get("uses") != checkout_pin:
        fail(f"called-workflow checkout is not SHA-pinned: {runtime_checkout.get('uses')!r}")
    if runtime_with.get("repository") != "${{ job.workflow_repository }}":
        fail(f"called-workflow repository context mismatch: {runtime_with.get('repository')!r}")
    if runtime_with.get("ref") != "${{ job.workflow_sha }}":
        fail(f"called-workflow SHA context mismatch: {runtime_with.get('ref')!r}")
    if runtime_with.get("path") != ".engineering-system-runtime":
        fail(f"runtime checkout path mismatch: {runtime_with.get('path')!r}")
    if runtime_with.get("persist-credentials") is not False:
        fail("runtime checkout must set persist-credentials false")
    expected_sparse = (
        "tools/ci_policy_audit.py\n"
        "tools/context_epoch.py\n"
        "tools/engineering-context.py\n"
        "tools/implementation_preflight.py\n"
        "tools/work_packet_authority.py\n"
    )
    if runtime_with.get("sparse-checkout") != expected_sparse:
        fail(f"sparse checkout mismatch: {runtime_with.get('sparse-checkout')!r}")
    if runtime_with.get("sparse-checkout-cone-mode") is not False:
        fail("sparse checkout cone mode must be false")
    if steps[5].get("run") != "python .engineering-system-runtime/tools/ci_policy_audit.py --root .":
        fail(f"auditor invocation mismatch: {steps[5].get('run')!r}")
    validation = steps[6].get("run") or ""
    if "ENGINEERING_SYSTEM_ADOPTION=PASS" not in validation:
        fail("adoption validation step was altered")
    for marker in (
        "import json",
        "def scenario_cost",
        "def pull_request_can_overlap_push",
        "def push_overlaps_unfiltered",
        "CI policy: DUPLICATE_NATIVE_CI",
        "CI policy: EXPENSIVE_DEFAULT_GATE",
    ):
        if marker in validation:
            fail(f"duplicated inline policy helper remains: {marker}")


def test_closed_pull_request_skips_expensive_pr_workflow() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_repo(
            root,
            workflows={
                "closed.yml": (
                    "name: closed\n"
                    "on:\n"
                    "  pull_request:\n"
                    "    types:\n"
                    "      - closed\n"
                    "jobs:\n"
                    "  test:\n"
                    "    runs-on: ubuntu-latest\n"
                    "    steps:\n"
                    "      - run: ./tests/run-all.sh\n"
                ),
            },
            mapped=[".github/workflows/closed.yml"],
            scenarios=[
                {
                    "id": "ADOPTED-TEST-001",
                    "level": "integration",
                    "command": "bash tests/run-all.sh",
                    "triggers": ["affected"],
                    "release_gate": True,
                }
            ],
        )
        result = run_audit(root)
        expected = [
            "VIOLATIONS=1",
            'EXPENSIVE_DEFAULT_GATE ADOPTED-TEST-001 cost=expensive triggers=[affected] command="bash tests/run-all.sh"',
        ]
        lines = result.stdout.splitlines()
        if any(line.startswith("EXPENSIVE_PR_WORKFLOW ") for line in lines):
            fail(f"closed pull_request emitted EXPENSIVE_PR_WORKFLOW:\n{result.stdout}")
        if result.returncode == 0 or lines != expected:
            fail(f"closed pull_request mismatch:\n{result.stdout}")


def test_list_form_on_overlaps() -> None:
    body = "name: lint\non: [push, pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_repo(
            root,
            workflows={"lint.yml": body},
            mapped=[".github/workflows/lint.yml"],
            scenarios=[],
        )
        result = run_audit(root)
        if result.stdout.splitlines() != [
            "VIOLATIONS=1",
            "DUPLICATE_NATIVE_CI .github/workflows/lint.yml",
        ]:
            fail(f"list-form on mismatch:\n{result.stdout}")


def main() -> int:
    test_drlink_shape_reports_five_violations()
    test_pr_only_cheap_passes()
    test_tags_only_push_plus_pr_passes()
    test_workflow_dispatch_and_schedule_pass()
    test_effective_cost_matches_engineering_test()
    test_expensive_affected_not_invoked_reports_default_gate()
    test_bash_vs_dot_slash_unconditional_pr_fails()
    test_conditional_job_and_step_pass()
    test_cheap_pr_command_passes()
    test_compound_shell_and_substring_pass()
    test_multiline_standalone_direct_script_passes()
    test_multiline_shell_if_passes()
    test_script_args_must_match()
    test_adoption_compliance_calls_runtime_auditor()
    test_closed_pull_request_skips_expensive_pr_workflow()
    test_list_form_on_overlaps()
    print("CI_POLICY_AUDIT_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
