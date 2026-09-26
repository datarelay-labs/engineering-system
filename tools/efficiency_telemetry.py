#!/usr/bin/env python3
"""Provider-neutral efficiency telemetry and cache-aware task budgets.

Records stay local, bounded, and content-free. Usage, cache, and cost stay
null unless a provider actually exposed them. Soft budget exhaustion yields
BLOCK instead of retrying. Nothing in this module exports over the network.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "efficiency-telemetry.schema.json"
GIT_RETENTION_PARTS = ("engineering-system", "telemetry")
DISABLED_NAME = "DISABLED"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_RECORDS = 32
MAX_RECORD_BYTES = 4096
PROFILE_FIELDS = ("provider", "model", "reasoning", "toolset")
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost",
)
COUNT_FIELDS = (
    "tool_turns",
    "retries",
    "rereads",
    "compactions",
    "pr_rework",
    "ci_rework",
    "review_rework",
    "human_interventions",
)
REWORK_COUNT_FIELDS = ("pr_rework", "ci_rework", "review_rework")
PROHIBITED_KEYS = frozenset(
    {
        "prompt",
        "prompts",
        "conversation",
        "transcript",
        "message",
        "messages",
        "source",
        "source_text",
        "code",
        "snippet",
        "diff",
        "tool_payload",
        "tool_payloads",
        "tool_output",
        "stdout",
        "stderr",
        "log",
        "logs",
        "secret",
        "secrets",
        "credential",
        "credentials",
        "api_key",
        "password",
        "env",
        "environment",
        "email",
        "chat",
        "path",
        "absolute_path",
        "cwd",
    }
)
SECRET_VALUE_RE = re.compile(r"(?:^|[^A-Za-z0-9])(?:sk-|ghp_|github_pat_|AKIA|Bearer |-----BEGIN)")
CONTRACT_TOKENS = (
    "engineering-system/telemetry/",
    "absolute-git-dir",
    "Do not estimate",
    "YIELD",
    "fail closed",
)


class TelemetryError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _validator() -> Draft202012Validator:
    return Draft202012Validator(_load_schema())


def _reject_prohibited(value: Any) -> None:
    if isinstance(value, dict):
        found = PROHIBITED_KEYS.intersection(value)
        if found:
            raise TelemetryError("PROHIBITED_FIELD")
        for item in value.values():
            _reject_prohibited(item)
    elif isinstance(value, list):
        for item in value:
            _reject_prohibited(item)
    elif isinstance(value, str):
        if value.startswith("/") or value.startswith("~") or "\\" in value or ".." in value:
            raise TelemetryError("LOCAL_PATH_PROHIBITED")
        if SECRET_VALUE_RE.search(value):
            raise TelemetryError("PROHIBITED_SECRET")


def _reject_nonfinite(value: Any) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise TelemetryError("NONFINITE_NUMBER")
        if value < 0:
            raise TelemetryError("NEGATIVE_NUMBER")
        return
    if isinstance(value, dict):
        for item in value.values():
            _reject_nonfinite(item)
    elif isinstance(value, list):
        for item in value:
            _reject_nonfinite(item)


def parse_document(instance: Any) -> dict[str, Any]:
    if not isinstance(instance, dict):
        raise TelemetryError("SCHEMA_INVALID")
    _reject_prohibited(instance)
    _reject_nonfinite(instance)
    if any(True for _ in _validator().iter_errors(instance)):
        raise TelemetryError("SCHEMA_INVALID")
    kind = instance.get("kind")
    if kind == "efficiency-telemetry":
        _enforce_record(instance)
    elif kind == "task-budget-decision":
        _enforce_decision(instance)
    return instance


def _enforce_record(record: dict[str, Any]) -> None:
    validation = record["validation"]
    if validation["evidence_state"] == "EXACT_HEAD" and not validation["exact_head"]:
        raise TelemetryError("EXACT_HEAD_REQUIRED")
    if record["terminal"] == "PASS":
        if (
            validation["outcome"] != "PASS"
            or validation["evidence_state"] != "EXACT_HEAD"
            or not validation["exact_head"]
        ):
            raise TelemetryError("TERMINAL_PASS_REQUIRES_EXACT_HEAD")
    _enforce_budget_shape(record["budget"], record["terminal"])


def _enforce_budget_shape(budget: dict[str, Any], terminal: str | None) -> None:
    state = budget["state"] if "state" in budget else None
    if state == "EXHAUSTED":
        if budget["disposition"] != "YIELD" or terminal != "BLOCK":
            raise TelemetryError("BUDGET_YIELD_REQUIRED")
        if budget["soft_limit"] is None or budget["consumed"] is None:
            raise TelemetryError("BUDGET_INCOMPLETE")
    elif state == "UNKNOWN":
        if budget["soft_limit"] is not None or budget["unit"] is not None or budget["consumed"] is not None:
            raise TelemetryError("BUDGET_FABRICATED")
        if budget["disposition"] != "CONTINUE":
            raise TelemetryError("BUDGET_INCOMPLETE")
    elif state == "WITHIN":
        if budget["soft_limit"] is None or budget["consumed"] is None or budget["unit"] is None:
            raise TelemetryError("BUDGET_INCOMPLETE")
        if budget["disposition"] != "CONTINUE":
            raise TelemetryError("BUDGET_INCOMPLETE")
        if budget["consumed"] >= budget["soft_limit"]:
            raise TelemetryError("BUDGET_YIELD_REQUIRED")


def _enforce_decision(decision: dict[str, Any]) -> None:
    if decision["state"] == "EXHAUSTED":
        if decision["disposition"] != "YIELD" or decision["terminal"] != "BLOCK":
            raise TelemetryError("BUDGET_YIELD_REQUIRED")
    elif decision["disposition"] == "YIELD" or decision["terminal"] is not None:
        raise TelemetryError("BUDGET_INCOMPLETE")


def usage_from_exposed(exposed: dict[str, Any] | None) -> dict[str, Any]:
    if exposed is None:
        exposed = {}
    if not isinstance(exposed, dict):
        raise TelemetryError("USAGE_INVALID")
    if any(str(key).startswith("estimated") or key == "inferred" for key in exposed):
        raise TelemetryError("USAGE_ESTIMATED")
    if set(exposed).difference(USAGE_FIELDS):
        raise TelemetryError("USAGE_UNKNOWN_FIELD")
    usage: dict[str, Any] = {}
    for key in USAGE_FIELDS:
        usage[key] = exposed[key] if key in exposed else None
    return usage


def normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, dict) or set(profile) != set(PROFILE_FIELDS):
        raise TelemetryError("PROFILE_INCOMPLETE")
    return {field: profile[field] for field in PROFILE_FIELDS}


class SessionProfile:
    """Stable provider/model/reasoning/toolset captured at session start."""

    def __init__(self, profile: dict[str, Any]):
        self.start = normalize_profile(profile)
        self.current = dict(self.start)
        self.switches: list[dict[str, Any]] = []

    def assign(self, field: str, value: Any) -> None:
        del field, value
        raise TelemetryError("SILENT_PROFILE_SWITCH")

    def switch(self, field: str, value: Any, justification: str) -> None:
        if field not in PROFILE_FIELDS:
            raise TelemetryError("PROFILE_FIELD_UNKNOWN")
        if not isinstance(justification, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,80}", justification):
            raise TelemetryError("PROFILE_SWITCH_UNJUSTIFIED")
        previous = self.current[field]
        if previous == value:
            return
        self.switches.append(
            {
                "field": field,
                "from": previous,
                "to": value,
                "justification": justification,
            }
        )
        self.current[field] = value


class TaskBudget:
    def __init__(self, soft_limit: int | None, unit: str | None):
        if soft_limit is None:
            if unit is not None:
                raise TelemetryError("BUDGET_UNIT_WITHOUT_LIMIT")
            self.soft_limit = None
            self.unit = None
            self.consumed = None
            self.state = "UNKNOWN"
            self.disposition = "CONTINUE"
            self._closed = False
            return
        if unit not in {"tokens", "cost_micros", "turns"} or not isinstance(soft_limit, int) or soft_limit < 1:
            raise TelemetryError("BUDGET_LIMIT_INVALID")
        self.soft_limit = soft_limit
        self.unit = unit
        self.consumed = 0
        self.state = "WITHIN"
        self.disposition = "CONTINUE"
        self._closed = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "soft_limit": self.soft_limit,
            "consumed": self.consumed,
            "unit": self.unit,
            "state": self.state,
            "disposition": self.disposition,
        }

    def consume(self, amount: int) -> dict[str, Any]:
        if self._closed or self.state == "EXHAUSTED":
            raise TelemetryError("RETRY_LOOP_BLOCKED")
        if not isinstance(amount, int) or amount < 1:
            raise TelemetryError("BUDGET_CONSUME_INVALID")
        if self.soft_limit is None:
            decision = {
                "schema_version": 1,
                "kind": "task-budget-decision",
                "state": "UNKNOWN",
                "disposition": "CONTINUE",
                "terminal": None,
            }
            return parse_document(decision)
        assert self.consumed is not None
        self.consumed += amount
        if self.consumed >= self.soft_limit:
            self.state = "EXHAUSTED"
            self.disposition = "YIELD"
            self._closed = True
        decision = {
            "schema_version": 1,
            "kind": "task-budget-decision",
            "state": self.state,
            "disposition": self.disposition,
            "terminal": "BLOCK" if self.state == "EXHAUSTED" else None,
        }
        return parse_document(decision)


def _counts(counts: dict[str, Any]) -> dict[str, int]:
    if not isinstance(counts, dict) or set(counts) != set(COUNT_FIELDS):
        raise TelemetryError("COUNTS_INCOMPLETE")
    return {field: counts[field] for field in COUNT_FIELDS}


def build_record(
    *,
    repo: str,
    workstream: str,
    task_kind: str,
    profile: SessionProfile | dict[str, Any],
    started_at: str,
    finished_at: str | None,
    duration_seconds: int | None,
    counts: dict[str, Any],
    validation: dict[str, Any],
    terminal: str,
    budget: TaskBudget | dict[str, Any],
    usage: dict[str, Any] | None = None,
    run_id: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    if isinstance(profile, SessionProfile):
        profile_doc = dict(profile.current)
        switches = list(profile.switches)
    else:
        profile_doc = normalize_profile(profile)
        switches = []
    budget_doc = budget.snapshot() if isinstance(budget, TaskBudget) else dict(budget)
    if budget_doc.get("state") == "EXHAUSTED":
        terminal = "BLOCK"
    record = {
        "schema_version": 1,
        "kind": "efficiency-telemetry",
        "run_id": run_id or uuid.uuid4().hex,
        "repo": repo,
        "workstream": workstream,
        "task_kind": task_kind,
        "profile": profile_doc,
        "profile_switches": switches,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": duration_seconds,
        "usage": usage_from_exposed(usage),
        "counts": _counts(counts),
        "validation": validation,
        "terminal": terminal,
        "budget": budget_doc,
    }
    parsed = parse_document(record)
    _verify_exact_head(ROOT if root is None else root, parsed)
    return parsed


def _git_output(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError as exc:
        raise TelemetryError("RETENTION_NOT_GIT_LOCAL") from exc
    if completed.returncode != 0:
        raise TelemetryError("RETENTION_NOT_GIT_LOCAL")
    return completed.stdout.strip()


def git_head(root: Path) -> str:
    try:
        head = _git_output(root, "rev-parse", "HEAD")
    except TelemetryError as exc:
        raise TelemetryError("EXACT_HEAD_UNVERIFIED") from exc
    if not SHA_RE.fullmatch(head):
        raise TelemetryError("EXACT_HEAD_UNVERIFIED")
    return head


def absolute_git_dir(root: Path) -> Path:
    raw = _git_output(root, "rev-parse", "--absolute-git-dir")
    git_dir = Path(raw).resolve()
    if not git_dir.is_dir():
        raise TelemetryError("RETENTION_NOT_GIT_LOCAL")
    return git_dir


def retention_directory(root: Path, *, create: bool = False) -> Path:
    git_dir = absolute_git_dir(root)
    directory = git_dir.joinpath(*GIT_RETENTION_PARTS)
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    resolved = directory.resolve()
    if not resolved.is_relative_to(git_dir):
        raise TelemetryError("RETENTION_NOT_GIT_LOCAL")
    return resolved


def _verify_exact_head(root: Path, record: dict[str, Any]) -> None:
    if record.get("kind") != "efficiency-telemetry":
        return
    validation = record["validation"]
    if record["terminal"] != "PASS" and validation["evidence_state"] != "EXACT_HEAD":
        return
    claimed = validation.get("exact_head")
    if not isinstance(claimed, str) or claimed != git_head(root):
        raise TelemetryError("EXACT_HEAD_UNVERIFIED")


def _porcelain(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError as exc:
        raise TelemetryError("RETENTION_NOT_GIT_LOCAL") from exc
    if completed.returncode != 0:
        raise TelemetryError("RETENTION_NOT_GIT_LOCAL")
    return completed.stdout


def telemetry_enabled(root: Path) -> bool:
    return not (retention_directory(root) / DISABLED_NAME).is_file()


def write_record(root: Path, record: dict[str, Any]) -> Path:
    record = parse_document(record)
    if record.get("kind") != "efficiency-telemetry":
        raise TelemetryError("SCHEMA_INVALID")
    _verify_exact_head(root, record)
    if not telemetry_enabled(root):
        raise TelemetryError("TELEMETRY_DISABLED")
    directory = retention_directory(root, create=True)
    encoded = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > MAX_RECORD_BYTES:
        raise TelemetryError("RETENTION_UNBOUNDED")
    destination = directory / f"{record['run_id']}.json"
    destination.write_bytes(encoded)
    if destination.name in _porcelain(root) or not destination.resolve().is_relative_to(absolute_git_dir(root)):
        destination.unlink(missing_ok=True)
        raise TelemetryError("RETENTION_NOT_GIT_LOCAL")
    files = sorted(directory.glob("*.json"), key=lambda item: (item.stat().st_mtime, item.name))
    while len(files) > MAX_RECORDS:
        files.pop(0).unlink()
    return destination


def rework_count(counts: dict[str, Any]) -> int:
    """Return the canonical P0b rework total for one telemetry record."""
    return sum(counts[field] for field in REWORK_COUNT_FIELDS)


def _worst(statuses: list[str]) -> str:
    if any(status == "FAIL" for status in statuses):
        return "FAIL"
    if any(status == "BLOCK" for status in statuses):
        return "BLOCK"
    if any(status == "UNKNOWN" for status in statuses):
        return "UNKNOWN"
    return "PASS"


def build_report(
    records: list[dict[str, Any]],
    *,
    head: str,
    evidence_state: str,
    root: Path | None = None,
) -> dict[str, Any]:
    root = ROOT if root is None else root
    parsed = [parse_document(record) for record in records]
    if not parsed:
        raise TelemetryError("REPORT_EMPTY")
    if evidence_state == "EXACT_HEAD":
        actual = git_head(root)
        if head != actual:
            raise TelemetryError("EXACT_HEAD_UNVERIFIED")
        for record in parsed:
            validation = record["validation"]
            if validation["evidence_state"] != "EXACT_HEAD" or validation["exact_head"] != actual:
                raise TelemetryError("EXACT_HEAD_MISMATCH")
            _verify_exact_head(root, record)
    costs = [record["usage"]["cost"] for record in parsed]
    durations = [record["duration_seconds"] for record in parsed]
    rework = 0
    humans = 0
    for record in parsed:
        counts = record["counts"]
        rework += rework_count(counts)
        humans += counts["human_interventions"]
    report = {
        "schema_version": 1,
        "kind": "efficiency-outcome-report",
        "head": head,
        "evidence_state": evidence_state,
        "terminal": _worst([record["terminal"] for record in parsed]),
        "validation_outcome": _worst([record["validation"]["outcome"] for record in parsed]),
        "record_count": len(parsed),
        "duration_seconds": None if any(item is None for item in durations) else sum(durations),
        "cost": None if any(item is None for item in costs) else sum(costs),
        "rework_count": rework,
        "human_interventions": humans,
    }
    if report["terminal"] == "PASS" and (
        evidence_state != "EXACT_HEAD" or report["validation_outcome"] != "PASS" or head != git_head(root)
    ):
        raise TelemetryError("TERMINAL_PASS_REQUIRES_EXACT_HEAD")
    return parse_document(report)


def contract_reasons(root: Path) -> list[str]:
    reasons: list[str] = []
    try:
        schema = _load_schema()
        record_schema = schema["$defs"]["record"]
        if record_schema.get("additionalProperties") is not False:
            reasons.append("FREEFORM_CONTENT_OPEN")
    except (OSError, json.JSONDecodeError, KeyError):
        return ["EFFICIENCY_CONTRACT_REGRESSION"]
    try:
        parse_document({"kind": "efficiency-telemetry", "prompt": "hidden"})
    except TelemetryError as exc:
        if exc.code != "PROHIBITED_FIELD":
            reasons.append("PRIVACY_CHECK_DRIFT")
    else:
        reasons.append("PRIVACY_LEAK_ACCEPTED")
    usage = usage_from_exposed({"input_tokens": 10})
    if any(usage[key] is not None for key in USAGE_FIELDS if key != "input_tokens"):
        reasons.append("USAGE_FABRICATED")
    budget = TaskBudget(1, "turns")
    decision = budget.consume(1)
    if decision["terminal"] != "BLOCK" or decision["disposition"] != "YIELD":
        reasons.append("BUDGET_YIELD_MISSING")
    try:
        budget.consume(1)
    except TelemetryError as exc:
        if exc.code != "RETRY_LOOP_BLOCKED":
            reasons.append("RETRY_LOOP_OPEN")
    else:
        reasons.append("RETRY_LOOP_OPEN")
    standard = root / "standards" / "SESSION_CONTINUITY.md"
    try:
        text = standard.read_text(encoding="utf-8")
    except OSError:
        reasons.append("EFFICIENCY_CONTRACT_REGRESSION")
    else:
        if any(token not in text for token in CONTRACT_TOKENS):
            reasons.append("EFFICIENCY_CONTRACT_REGRESSION")
    try:
        retention_directory(root)
    except TelemetryError:
        reasons.append("RETENTION_NOT_GIT_LOCAL")
    return list(dict.fromkeys(reasons))


def _emit(document: dict[str, Any]) -> None:
    print(json.dumps(document, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("gate")
    validate = sub.add_parser("validate-record")
    validate.add_argument("--record", type=Path, required=True)
    validate.add_argument("--root", type=Path, default=ROOT)
    report = sub.add_parser("report")
    report.add_argument("--root", type=Path, default=ROOT)
    report.add_argument("--head", required=True)
    report.add_argument("--evidence-state", required=True, choices=("EXACT_HEAD", "STALE", "MISSING"))
    args = parser.parse_args(argv)
    try:
        if args.command == "gate":
            reasons = contract_reasons(ROOT)
            status = "PASS" if not reasons else "BLOCK"
            _emit({"kind": "efficiency-telemetry-gate", "reasons": reasons, "status": status})
            return 0 if status == "PASS" else 1
        if args.command == "validate-record":
            instance = json.loads(args.record.read_text(encoding="utf-8"))
            parsed = parse_document(instance)
            _verify_exact_head(args.root, parsed)
            _emit(parsed)
            return 0
        directory = retention_directory(args.root)
        records = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))]
        _emit(build_report(records, head=args.head, evidence_state=args.evidence_state, root=args.root))
        return 0
    except TelemetryError as exc:
        print(f"FAIL {exc.code}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
