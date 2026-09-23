#!/usr/bin/env python3
"""Optional knowledge-index freshness and evidence-triggered retrieval routing.

The index is optional. Local search stays the default. This tool does not
mutate the repository and does not call a retrieval vendor or network API.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

BREADTH_FILES = 25
REREAD_THRESHOLD = 3
DEFAULT_MAX_FINDINGS = 20
INDEX_REL = Path(".engineering") / "knowledge.yaml"
SCHEMA_REL = Path("schemas") / "knowledge-index.schema.json"


def fail_usage(message: str) -> None:
    raise SystemExit(f"KNOWLEDGE_CONTRACT=FAIL {message}")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_relative(root: Path, raw: object) -> tuple[Path | None, str | None]:
    if not isinstance(raw, str) or not raw or raw.startswith(("/", "\\")) or "\\" in raw:
        return None, "UNSAFE_PATH"
    parts = Path(raw).parts
    if any(part in ("", ".", "..") for part in parts):
        return None, "UNSAFE_PATH"
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None, "UNSAFE_PATH"
    return candidate, None


def finding(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail[:180]}


def schema_findings(root: Path, instance: object) -> list[dict[str, str]]:
    schema_path = root / SCHEMA_REL
    if not schema_path.is_file():
        return [finding("SCHEMA", f"missing {SCHEMA_REL.as_posix()}")]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(instance),
        key=lambda item: list(item.path),
    )
    return [finding("SCHEMA", error.message.replace("\n", " ")) for error in errors]


def check_index(root: Path) -> dict[str, object]:
    index_path = root / INDEX_REL
    if not index_path.is_file():
        return {"knowledge_index": "ABSENT", "result": "PASS", "findings": []}
    try:
        instance = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return {
            "knowledge_index": "PRESENT",
            "result": "FAIL",
            "findings": [finding("SCHEMA", str(exc).splitlines()[0])],
        }
    errors = schema_findings(root, instance)
    if errors or not isinstance(instance, dict):
        return {
            "knowledge_index": "PRESENT",
            "result": "FAIL",
            "findings": errors or [finding("SCHEMA", "index must be a mapping")],
        }

    findings: list[dict[str, str]] = []
    canonical: set[str] = set()
    for domain in instance.get("domains") or []:
        for raw in domain.get("canonical") or []:
            path, problem = safe_relative(root, raw)
            if problem:
                findings.append(finding(problem, str(raw)))
                continue
            assert path is not None
            canonical.add(str(raw))
            if not path.is_file():
                findings.append(finding("MISSING", str(raw)))

    for item in instance.get("derived") or []:
        raw = item.get("path")
        path, problem = safe_relative(root, raw)
        if problem:
            findings.append(finding(problem, str(raw)))
            continue
        assert path is not None
        if str(raw) in canonical:
            findings.append(finding("CONFLICT", str(raw)))
        elif not path.is_file():
            findings.append(finding("MISSING", str(raw)))

    for item in instance.get("generated") or []:
        raw = item.get("path")
        source = item.get("source")
        path, problem = safe_relative(root, raw)
        source_path, source_problem = safe_relative(root, source)
        if problem:
            findings.append(finding(problem, str(raw)))
        elif str(raw) in canonical:
            findings.append(finding("CONFLICT", str(raw)))
        elif path is not None and not path.is_file():
            findings.append(finding("MISSING", str(raw)))
        if source_problem:
            findings.append(finding(source_problem, str(source)))
        elif source_path is not None and not source_path.is_file():
            findings.append(finding("MISSING_SOURCE", str(source)))
        elif source_path is not None and isinstance(item.get("source_sha256"), str):
            actual = sha256_file(source_path)
            if actual != item["source_sha256"]:
                findings.append(finding("STALE_GENERATED", str(raw)))

    ordered = sorted(findings, key=lambda item: (item["code"], item["detail"]))
    return {
        "knowledge_index": "PRESENT",
        "result": "FAIL" if ordered else "PASS",
        "findings": ordered,
    }


def command_for(root: Path, mode: str) -> str:
    command = f"python3 tools/knowledge-contract.py check --{mode}"
    if root.resolve() != Path.cwd().resolve():
        command += " --root " + shlex.quote(str(root))
    return command


def emit_check(root: Path, report: dict[str, object], max_findings: int, mode: str) -> None:
    if mode == "raw":
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return
    findings = list(report["findings"])
    shown = findings if mode == "full" else findings[:max_findings]
    print(f"KNOWLEDGE_INDEX={report['knowledge_index']}")
    print(f"RESULT={report['result']}")
    print(f"FINDING_COUNT={len(findings)}")
    for item in shown:
        print(f"FINDING={item['code']} {item['detail']}")
    hidden = len(findings) - len(shown)
    if hidden:
        print(f"FINDINGS_TRUNCATED={hidden}")
        print(f"FULLER={command_for(root, 'full')}")
        print(f"RAW={command_for(root, 'raw')}")


def parse_signals(payload: object) -> tuple[dict[str, object] | None, str | None]:
    if not isinstance(payload, dict):
        return None, "signals must be a mapping"
    allowed = {"files_consulted", "repos", "reread"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        return None, "unknown signal " + unknown[0]
    files = payload.get("files_consulted", 0)
    repos = payload.get("repos", [])
    reread = payload.get("reread", {})
    if isinstance(files, bool) or not isinstance(files, int) or files < 0:
        return None, "files_consulted must be a non-negative integer"
    if not isinstance(repos, list) or not all(isinstance(item, str) and item for item in repos):
        return None, "repos must be a list of strings"
    if not isinstance(reread, dict):
        return None, "reread must be a mapping"
    counts: dict[str, int] = {}
    for key, value in reread.items():
        if not isinstance(key, str) or not key or isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None, "reread counts must be non-negative integers"
        counts[key] = value
    return {"files_consulted": files, "repos": repos, "reread": counts}, None


def route_signals(signals: dict[str, object]) -> dict[str, object]:
    reasons: list[str] = []
    if int(signals["files_consulted"]) >= BREADTH_FILES:
        reasons.append("breadth")
    if len(set(signals["repos"])) >= 2:
        reasons.append("cross_repo")
    if any(int(count) >= REREAD_THRESHOLD for count in signals["reread"].values()):
        reasons.append("repeated_reread")
    return {
        "retrieval": "ESCALATE" if reasons else "LOCAL",
        "reasons": reasons,
        "signals": signals,
    }


def emit_route(report: dict[str, object], raw: bool) -> None:
    if raw:
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return
    print(f"RETRIEVAL={report['retrieval']}")
    reasons = list(report["reasons"])
    print(f"REASON_COUNT={len(reasons)}")
    for reason in reasons:
        print(f"REASON={reason}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Knowledge freshness and retrieval routing")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--root", default=".")
    check.add_argument("--max-findings", type=int, default=DEFAULT_MAX_FINDINGS)
    mode = check.add_mutually_exclusive_group()
    mode.add_argument("--full", action="store_true")
    mode.add_argument("--raw", action="store_true")
    route = sub.add_parser("route")
    route.add_argument("--signals", default="")
    route.add_argument("--raw", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        if args.max_findings < 0:
            fail_usage("max-findings must be non-negative")
        root = Path(args.root).resolve()
        report = check_index(root)
        mode = "raw" if args.raw else "full" if args.full else "bounded"
        emit_check(root, report, args.max_findings, mode)
        return 0 if report["result"] == "PASS" else 1

    if args.signals:
        signal_path = Path(args.signals)
        try:
            payload = yaml.safe_load(signal_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            print("RETRIEVAL=BLOCKED")
            print("REASON=INVALID_SIGNALS")
            return 1
    else:
        payload = {}
    signals, problem = parse_signals(payload)
    if problem or signals is None:
        print("RETRIEVAL=BLOCKED")
        print("REASON=INVALID_SIGNALS")
        print(f"DETAIL={problem}")
        return 1
    report = route_signals(signals)
    emit_route(report, args.raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
