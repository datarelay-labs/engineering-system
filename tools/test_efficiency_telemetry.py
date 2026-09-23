#!/usr/bin/env python3
"""Regressions for local efficiency telemetry, privacy, and task budgets."""
from __future__ import annotations

import importlib.util
import inspect
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "efficiency_telemetry.py"

_SPEC = importlib.util.spec_from_file_location("efficiency_telemetry", TOOL)
assert _SPEC and _SPEC.loader
telemetry = importlib.util.module_from_spec(_SPEC)
sys.modules["efficiency_telemetry"] = telemetry
_SPEC.loader.exec_module(telemetry)

_BEHAVIOR_SPEC = importlib.util.spec_from_file_location(
    "behavior_eval_for_telemetry",
    ROOT / "tools" / "behavior_eval.py",
)
assert _BEHAVIOR_SPEC and _BEHAVIOR_SPEC.loader
behavior_eval = importlib.util.module_from_spec(_BEHAVIOR_SPEC)
sys.modules["behavior_eval_for_telemetry"] = behavior_eval
_BEHAVIOR_SPEC.loader.exec_module(behavior_eval)

STARTED = "2026-09-23T00:00:00Z"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _git_head(root: Path = ROOT) -> str:
    completed = _git(root, "rev-parse", "HEAD")
    if completed.returncode != 0 or len(completed.stdout.strip()) != 40:
        _fail(f"git HEAD unavailable for {root}: {completed.stderr}")
    return completed.stdout.strip()


def _init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    init = subprocess.run(
        ["git", "init", "-b", "main", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if init.returncode != 0:
        _fail(f"git init failed: {init.stderr}")
    commit = _git(
        path,
        "-c",
        "user.email=telemetry@example.com",
        "-c",
        "user.name=Telemetry",
        "commit",
        "--allow-empty",
        "-m",
        "init",
    )
    if commit.returncode != 0:
        _fail(f"git commit failed: {commit.stderr}")
    return _git_head(path)


def _fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


HEAD = _git_head()


def _counts() -> dict[str, int]:
    return {field: 0 for field in telemetry.COUNT_FIELDS}


def _validation(outcome: str = "PASS", head: str | None = None) -> dict:
    return {
        "ids": ["ENG-TELEMETRY-001"],
        "exact_head": HEAD if head is None else head,
        "evidence_state": "EXACT_HEAD",
        "outcome": outcome,
    }


def _missing_validation() -> dict:
    return {
        "ids": ["ENG-TELEMETRY-001"],
        "exact_head": None,
        "evidence_state": "MISSING",
        "outcome": "UNKNOWN",
    }


def _profile() -> dict[str, str | None]:
    return {"provider": "cursor", "model": "auto", "reasoning": "low", "toolset": "default"}


def _record(**overrides):
    budget = overrides.pop("budget", telemetry.TaskBudget(None, None))
    record = telemetry.build_record(
        repo=overrides.pop("repo", "datarelay-labs/engineering-system"),
        workstream=overrides.pop("workstream", "engineering-system-1-7-p0b"),
        task_kind=overrides.pop("task_kind", "DEVELOPMENT"),
        profile=overrides.pop("profile", _profile()),
        started_at=overrides.pop("started_at", STARTED),
        finished_at=overrides.pop("finished_at", STARTED),
        duration_seconds=overrides.pop("duration_seconds", 5),
        counts=overrides.pop("counts", _counts()),
        validation=overrides.pop("validation", _validation()),
        terminal=overrides.pop("terminal", "PASS"),
        budget=budget,
        usage=overrides.pop("usage", None),
        run_id=overrides.pop("run_id", "b" * 32),
        root=overrides.pop("root", None),
    )
    if overrides:
        _fail(f"unused record overrides: {sorted(overrides)}")
    return record


def test_schema_rejects_freeform_and_prohibited_content() -> None:
    record = _record()
    for key, value, code in (
        ("prompt", "hidden prompt", "PROHIBITED_FIELD"),
        ("source", "print(1)", "PROHIBITED_FIELD"),
        ("tool_payload", {"cmd": "ls"}, "PROHIBITED_FIELD"),
        ("secret", "abc", "PROHIBITED_FIELD"),
        ("note", "freeform", "SCHEMA_INVALID"),
    ):
        leaked = json.loads(json.dumps(record))
        leaked[key] = value
        try:
            telemetry.parse_document(leaked)
        except telemetry.TelemetryError as exc:
            if exc.code != code:
                _fail(f"{key} returned {exc.code}")
        else:
            _fail(f"{key} was accepted")
    leaked = json.loads(json.dumps(record))
    leaked["profile"]["model"] = "sk-live-token"
    try:
        telemetry.parse_document(leaked)
    except telemetry.TelemetryError as exc:
        if exc.code != "PROHIBITED_SECRET":
            _fail(f"secret value returned {exc.code}")
    else:
        _fail("secret value was accepted")
    leaked = json.loads(json.dumps(record))
    leaked["profile"]["toolset"] = "/tmp/private"
    try:
        telemetry.parse_document(leaked)
    except telemetry.TelemetryError as exc:
        if exc.code != "LOCAL_PATH_PROHIBITED":
            _fail(f"path value returned {exc.code}")
    else:
        _fail("absolute path was accepted")


def test_missing_usage_stays_null() -> None:
    usage = telemetry.usage_from_exposed({"input_tokens": 12})
    if usage["input_tokens"] != 12:
        _fail("exposed input tokens were dropped")
    for key in ("output_tokens", "cache_read_tokens", "cache_write_tokens", "cost"):
        if usage[key] is not None:
            _fail(f"{key} was fabricated")
    record = _record(usage={"input_tokens": 12, "cost": None})
    if record["usage"]["cost"] is not None or record["usage"]["cache_read_tokens"] is not None:
        _fail("explicit null usage was inferred")
    try:
        telemetry.usage_from_exposed({"estimated_cost": 1})
    except telemetry.TelemetryError as exc:
        if exc.code != "USAGE_ESTIMATED":
            _fail(f"estimated usage returned {exc.code}")
    else:
        _fail("estimated usage was accepted")


def test_profile_switch_requires_justification() -> None:
    session = telemetry.SessionProfile(_profile())
    if session.current != session.start:
        _fail("session start profile drifted")
    try:
        session.assign("model", "other")
    except telemetry.TelemetryError as exc:
        if exc.code != "SILENT_PROFILE_SWITCH":
            _fail(f"silent switch returned {exc.code}")
    else:
        _fail("silent profile switch was accepted")
    if session.current["model"] != "auto":
        _fail("rejected switch mutated the profile")
    session.switch("model", "sonnet", "CORRECTNESS_REQUIRED")
    record = _record(profile=session)
    if record["profile"]["model"] != "sonnet":
        _fail("justified switch was not recorded")
    if record["profile_switches"] != [
        {
            "field": "model",
            "from": "auto",
            "to": "sonnet",
            "justification": "CORRECTNESS_REQUIRED",
        }
    ]:
        _fail("profile switch metadata drifted")
    try:
        session.switch("reasoning", "high", "")
    except telemetry.TelemetryError as exc:
        if exc.code != "PROFILE_SWITCH_UNJUSTIFIED":
            _fail(f"empty justification returned {exc.code}")
    else:
        _fail("empty justification was accepted")


def test_budget_exhaustion_yields_and_blocks_retries() -> None:
    budget = telemetry.TaskBudget(2, "turns")
    first = budget.consume(1)
    if first["disposition"] != "CONTINUE" or first["terminal"] is not None:
        _fail("budget consumed inside the soft limit yielded early")
    decision = budget.consume(1)
    if decision["state"] != "EXHAUSTED" or decision["disposition"] != "YIELD" or decision["terminal"] != "BLOCK":
        _fail(f"exhaustion decision drifted: {decision}")
    try:
        budget.consume(1)
    except telemetry.TelemetryError as exc:
        if exc.code != "RETRY_LOOP_BLOCKED":
            _fail(f"retry returned {exc.code}")
    else:
        _fail("retry after exhaustion was accepted")
    if budget.consumed != 2:
        _fail("blocked retry mutated consumption")
    open_budget = telemetry.TaskBudget(None, None)
    continued = open_budget.consume(5)
    if continued["state"] != "UNKNOWN" or continued["disposition"] != "CONTINUE" or open_budget.consumed is not None:
        _fail("missing soft limit fabricated a budget")
    record = _record(budget=budget, terminal="PASS")
    if record["terminal"] != "BLOCK" or record["budget"]["disposition"] != "YIELD":
        _fail("exhausted budget did not force BLOCK/YIELD")


def _expect_error(code: str, func) -> None:
    try:
        func()
    except telemetry.TelemetryError as exc:
        if exc.code != code:
            _fail(f"expected {code} but returned {exc.code}")
    else:
        _fail(f"{code} was accepted")


def test_exact_head_pass_matches_git() -> None:
    fake = "f" * 40
    _expect_error(
        "EXACT_HEAD_UNVERIFIED",
        lambda: _record(validation=_validation(head=fake)),
    )
    passing = _record()
    _expect_error(
        "EXACT_HEAD_UNVERIFIED",
        lambda: telemetry.build_report([passing], head=fake, evidence_state="EXACT_HEAD"),
    )
    mismatched = json.loads(json.dumps(passing))
    mismatched["validation"]["exact_head"] = fake
    _expect_error(
        "EXACT_HEAD_MISMATCH",
        lambda: telemetry.build_report([mismatched], head=HEAD, evidence_state="EXACT_HEAD"),
    )
    _expect_error(
        "TERMINAL_PASS_REQUIRES_EXACT_HEAD",
        lambda: _record(validation=_validation("FAIL")),
    )
    _expect_error(
        "TERMINAL_PASS_REQUIRES_EXACT_HEAD",
        lambda: _record(terminal="PASS", validation=_missing_validation()),
    )
    failed = json.loads(json.dumps(passing))
    failed["validation"]["outcome"] = "FAIL"
    _expect_error(
        "TERMINAL_PASS_REQUIRES_EXACT_HEAD",
        lambda: telemetry.build_report([failed], head=HEAD, evidence_state="EXACT_HEAD"),
    )


def test_nonfinite_numbers_fail_closed() -> None:
    for value in (float("nan"), float("inf"), float("-inf")):
        leaked = json.loads(json.dumps(_record()))
        leaked["usage"]["cost"] = value
        _expect_error("NONFINITE_NUMBER", lambda leaked=leaked: telemetry.parse_document(leaked))
    leaked = json.loads(json.dumps(_record()))
    leaked["usage"]["cost"] = -1
    _expect_error("NEGATIVE_NUMBER", lambda: telemetry.parse_document(leaked))


def test_retention_is_git_local_bounded_and_not_negatable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _expect_error(
            "RETENTION_NOT_GIT_LOCAL",
            lambda: telemetry.write_record(root, _record(terminal="BLOCK", validation=_missing_validation())),
        )
        head = _init_repo(root)
        (root / ".gitignore").write_text(
            ".engineering/telemetry/\n!.engineering/telemetry/\n!.engineering/telemetry/**\n",
            encoding="utf-8",
        )
        (root / ".engineering" / "telemetry").mkdir(parents=True)
        record = _record(root=root, validation=_validation(head=head))
        destination = telemetry.write_record(root, record)
        body = destination.read_text(encoding="utf-8")
        if "prompt" in body or "tool_payload" in body or "/home/" in body:
            _fail("persisted record contained prohibited content")
        git_dir = telemetry.absolute_git_dir(root)
        if not destination.is_relative_to(git_dir / "engineering-system" / "telemetry"):
            _fail("record escaped the git-local retention directory")
        if destination.is_relative_to(root / ".engineering"):
            _fail("record was written into the tracked worktree")
        porcelain = _git(root, "status", "--porcelain", "--untracked-files=all").stdout
        if destination.name in porcelain:
            _fail(f"git status exposed generated telemetry: {porcelain}")
        original_max = telemetry.MAX_RECORD_BYTES
        telemetry.MAX_RECORD_BYTES = 10
        try:
            telemetry.write_record(root, _record(root=root, validation=_validation(head=head), run_id="c" * 32))
        except telemetry.TelemetryError as exc:
            if exc.code != "RETENTION_UNBOUNDED":
                _fail(f"oversize record returned {exc.code}")
        else:
            _fail("oversize record was written")
        finally:
            telemetry.MAX_RECORD_BYTES = original_max
        for index in range(telemetry.MAX_RECORDS + 2):
            written = telemetry.write_record(
                root,
                _record(root=root, validation=_validation(head=head), run_id=f"{index:032x}"),
            )
            os.utime(written, (index + 1, index + 1))
        kept = list((git_dir / "engineering-system" / "telemetry").glob("*.json"))
        if len(kept) != telemetry.MAX_RECORDS:
            _fail(f"retention kept {len(kept)} records")
        (git_dir / "engineering-system" / "telemetry" / "DISABLED").write_text("1\n", encoding="utf-8")
        try:
            telemetry.write_record(root, _record(root=root, validation=_validation(head=head), run_id="d" * 32))
        except telemetry.TelemetryError as exc:
            if exc.code != "TELEMETRY_DISABLED":
                _fail(f"disabled telemetry returned {exc.code}")
        else:
            _fail("disabled telemetry still wrote a record")


def test_linked_worktrees_keep_distinct_git_local_retention() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp)
        origin = parent / "origin"
        head = _init_repo(origin)
        worktree = parent / "linked"
        added = _git(origin, "worktree", "add", "--detach", str(worktree), "HEAD")
        if added.returncode != 0:
            _fail(f"worktree add failed: {added.stderr}")
        origin_record = telemetry.write_record(
            origin,
            _record(root=origin, validation=_validation(head=head), run_id="1" * 32),
        )
        linked_record = telemetry.write_record(
            worktree,
            _record(root=worktree, validation=_validation(head=head), run_id="2" * 32),
        )
        if origin_record.parent == linked_record.parent:
            _fail("linked worktrees shared one telemetry directory")
        for repo, destination in ((origin, origin_record), (worktree, linked_record)):
            porcelain = _git(repo, "status", "--porcelain", "--untracked-files=all").stdout
            if destination.name in porcelain:
                _fail(f"linked worktree exposed telemetry: {porcelain}")


def test_report_keeps_exact_head_and_unknown_cost() -> None:
    passing = _record(usage={"input_tokens": 3})
    report = telemetry.build_report([passing], head=HEAD, evidence_state="EXACT_HEAD")
    if report["head"] != HEAD or report["evidence_state"] != "EXACT_HEAD" or report["terminal"] != "PASS":
        _fail("outcome report dropped exact-head PASS state")
    if report["cost"] is not None or report["validation_outcome"] != "PASS":
        _fail("unknown cost was fabricated in the report")
    blocked = _record(run_id="e" * 32, terminal="BLOCK", validation=_validation("BLOCK"))
    mixed = telemetry.build_report([passing, blocked], head=HEAD, evidence_state="EXACT_HEAD")
    if mixed["terminal"] != "BLOCK" or mixed["validation_outcome"] != "BLOCK":
        _fail("BLOCK outcome was not reported")
    try:
        telemetry.build_report([passing], head="b" * 40, evidence_state="EXACT_HEAD")
    except telemetry.TelemetryError as exc:
        if exc.code != "EXACT_HEAD_UNVERIFIED":
            _fail(f"head mismatch returned {exc.code}")
    else:
        _fail("mismatched exact head was reported as EXACT_HEAD")


def test_behavior_rollout_gate_includes_efficiency_contract() -> None:
    source = inspect.getsource(behavior_eval.evaluate_gate)
    if "efficiency_contract_reasons" not in source:
        _fail("behavior rollout gate does not consult the efficiency contract")
    if behavior_eval.efficiency_contract_reasons(ROOT):
        _fail(f"canonical contract regressed: {behavior_eval.efficiency_contract_reasons(ROOT)}")
    with tempfile.TemporaryDirectory() as tmp:
        reasons = behavior_eval.efficiency_contract_reasons(Path(tmp))
        if "EFFICIENCY_CONTRACT_REGRESSION" not in reasons or "RETENTION_NOT_GIT_LOCAL" not in reasons:
            _fail(f"incomplete repository did not fail closed: {reasons}")
    catalog = behavior_eval.load_catalog()
    document = {
        "schema_version": 1,
        "kind": "behavior-eval-run",
        "runner": "deterministic",
        "head": HEAD,
        "provider": None,
        "model": None,
        "scenarios": [
            {
                "id": item["id"],
                "mandatory": True,
                "safety": True,
                "status": "PASS",
                "evidence": "CONTEXT_ROUTING_OK",
            }
            for item in catalog
        ],
    }
    gate = behavior_eval.evaluate_gate(document, expected_head=HEAD, baseline_status="PASS")
    if gate["status"] != "PASS" or gate["reasons"]:
        _fail(f"healthy efficiency contract blocked behavior rollout: {gate['reasons']}")


def test_validate_record_cli_rejects_fake_exact_head() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        fake = json.loads(json.dumps(_record()))
        fake["validation"]["exact_head"] = "f" * 40
        fake_path = directory / "fake.json"
        fake_path.write_text(json.dumps(fake), encoding="utf-8")
        rejected = subprocess.run(
            ["python3", str(TOOL), "validate-record", "--record", str(fake_path), "--root", str(ROOT)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if rejected.returncode == 0 or "EXACT_HEAD_UNVERIFIED" not in rejected.stdout:
            _fail(f"fake exact-head PASS was accepted: {rejected.stdout} {rejected.stderr}")
        real_path = directory / "real.json"
        real_path.write_text(json.dumps(_record()), encoding="utf-8")
        accepted = subprocess.run(
            ["python3", str(TOOL), "validate-record", "--record", str(real_path), "--root", str(ROOT)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if accepted.returncode != 0:
            _fail(f"git-matching exact-head record was rejected: {accepted.stdout} {accepted.stderr}")


def test_gate_command_passes_offline() -> None:
    completed = subprocess.run(
        ["python3", str(TOOL), "gate"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        _fail(f"gate command failed: {completed.stdout} {completed.stderr}")
    payload = json.loads(completed.stdout)
    if payload["status"] != "PASS" or payload["reasons"]:
        _fail(f"gate payload drifted: {payload}")
    if "http://" in TOOL.read_text(encoding="utf-8") or "urllib" in TOOL.read_text(encoding="utf-8"):
        _fail("telemetry tool gained a network export path")


def main() -> None:
    test_schema_rejects_freeform_and_prohibited_content()
    test_missing_usage_stays_null()
    test_profile_switch_requires_justification()
    test_budget_exhaustion_yields_and_blocks_retries()
    test_exact_head_pass_matches_git()
    test_nonfinite_numbers_fail_closed()
    test_retention_is_git_local_bounded_and_not_negatable()
    test_linked_worktrees_keep_distinct_git_local_retention()
    test_report_keeps_exact_head_and_unknown_cost()
    test_validate_record_cli_rejects_fake_exact_head()
    test_behavior_rollout_gate_includes_efficiency_contract()
    test_gate_command_passes_offline()
    print("PASS efficiency telemetry")


if __name__ == "__main__":
    main()
