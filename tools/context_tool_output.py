#!/usr/bin/env python3
"""Deterministic reversible reduction for line-oriented tool output."""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import context_compiler
import context_fold

MAX_INPUT_BYTES = context_fold.MAX_ENTRY_BYTES
MAX_INPUT_LINES = 20000
MAX_VISIBLE_LINES = 80
MAX_VISIBLE_BYTES = 32768
MAX_HEAD_TAIL_LINES = 8
MAX_CONTEXT_LINES = 4
MAX_VISIBLE_LINE_BYTES = 4096

class ToolOutputError(ValueError):
    pass

REQUEST_FIELDS = frozenset(
    {
        "task",
        "text",
        "protected",
        "enabled",
        "max_lines",
        "max_bytes",
        "head_lines",
        "tail_lines",
        "context_lines",
    }
)
DIAGNOSTIC_TERMS = frozenset(
    {
        "error",
        "errors",
        "fail",
        "failed",
        "failure",
        "fatal",
        "panic",
        "exception",
        "traceback",
        "warning",
        "warnings",
    }
)


def _utf8_bytes(value: Any, *, code: str) -> bytes:
    if not isinstance(value, str):
        raise ToolOutputError(code)
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ToolOutputError(code) from exc


def _bounded_int(raw: Any, *, code: str, minimum: int, maximum: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ToolOutputError(code)
    if raw < minimum or raw > maximum:
        raise ToolOutputError(code)
    return raw


def _parse_request(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != REQUEST_FIELDS:
        raise ToolOutputError("REQUEST_INVALID")
    enabled = raw["enabled"]
    protected = raw["protected"]
    if not isinstance(enabled, bool):
        raise ToolOutputError("ENABLED_INVALID")
    if not isinstance(protected, bool):
        raise ToolOutputError("PROTECTED_INVALID")

    text_bytes = _utf8_bytes(raw["text"], code="TEXT_INVALID")
    if not text_bytes or len(text_bytes) > MAX_INPUT_BYTES:
        raise ToolOutputError("TEXT_SIZE_INVALID")
    text = raw["text"]
    lines = text.splitlines()
    if len(lines) > MAX_INPUT_LINES:
        raise ToolOutputError("TEXT_LINES_INVALID")

    task_bytes = _utf8_bytes(raw["task"], code="TASK_INVALID")
    if enabled and (
        not raw["task"].strip()
        or len(task_bytes) > context_compiler.MAX_TASK_BYTES
    ):
        raise ToolOutputError("TASK_INVALID")
    if not enabled and len(task_bytes) > context_compiler.MAX_TASK_BYTES:
        raise ToolOutputError("TASK_INVALID")

    max_lines = _bounded_int(
        raw["max_lines"],
        code="MAX_LINES_INVALID",
        minimum=1,
        maximum=MAX_VISIBLE_LINES,
    )
    max_bytes = _bounded_int(
        raw["max_bytes"],
        code="MAX_BYTES_INVALID",
        minimum=1,
        maximum=MAX_VISIBLE_BYTES,
    )

    head_lines = _bounded_int(
        raw["head_lines"],
        code="HEAD_LINES_INVALID",
        minimum=0,
        maximum=MAX_HEAD_TAIL_LINES,
    )
    tail_lines = _bounded_int(
        raw["tail_lines"],
        code="TAIL_LINES_INVALID",
        minimum=0,
        maximum=MAX_HEAD_TAIL_LINES,
    )
    context_lines = _bounded_int(
        raw["context_lines"],
        code="CONTEXT_LINES_INVALID",
        minimum=0,
        maximum=MAX_CONTEXT_LINES,
    )
    if head_lines + tail_lines > max_lines:
        raise ToolOutputError("VISIBLE_LINE_BUDGET_TOO_SMALL")

    return {
        "task": raw["task"],
        "text": text,
        "text_bytes": text_bytes,
        "lines": lines,
        "protected": protected,
        "enabled": enabled,
        "max_lines": max_lines,
        "max_bytes": max_bytes,
        "head_lines": head_lines,
        "tail_lines": tail_lines,
        "context_lines": context_lines,
    }


def _line_size(line: str) -> int:
    try:
        return len(line.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ToolOutputError("TEXT_INVALID") from exc


def _require_visible_line(line: str) -> int:
    size = _line_size(line)
    if size > MAX_VISIBLE_LINE_BYTES:
        raise ToolOutputError("VISIBLE_LINE_TOO_LARGE")
    return size


def _line_priority(line: str, task_terms: frozenset[str]) -> int:
    line_terms = context_compiler.terms(line)
    task_overlap = len(task_terms & line_terms)
    diagnostic_overlap = len(DIAGNOSTIC_TERMS & line_terms)
    return (task_overlap * 100) + (diagnostic_overlap * 25)


def _omitted_ranges(total_lines: int, selected: set[int]) -> list[dict[str, int]]:
    ranges: list[dict[str, int]] = []
    start: int | None = None
    for index in range(total_lines):
        if index not in selected and start is None:
            start = index
        if index in selected and start is not None:
            ranges.append(
                {
                    "start_line": start + 1,
                    "end_line": index,
                    "count": index - start,
                }
            )
            start = None
    if start is not None:
        ranges.append(
            {
                "start_line": start + 1,
                "end_line": total_lines,
                "count": total_lines - start,
            }
        )
    return ranges


def _select_lines(parsed: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, int]], int, int]:
    lines: list[str] = parsed["lines"]
    max_lines: int = parsed["max_lines"]
    max_bytes: int = parsed["max_bytes"]
    selected: set[int] = set()
    selected_bytes = 0

    def add(index: int, *, required: bool) -> bool:
        nonlocal selected_bytes
        if index < 0 or index >= len(lines) or index in selected:
            return True
        size = _line_size(lines[index])
        if size > MAX_VISIBLE_LINE_BYTES:
            if required:
                raise ToolOutputError("VISIBLE_LINE_TOO_LARGE")
            return False
        if len(selected) >= max_lines or selected_bytes + size > max_bytes:
            if required:
                raise ToolOutputError("VISIBLE_BUDGET_TOO_SMALL")
            return False
        selected.add(index)
        selected_bytes += size
        return True

    for index in range(min(parsed["head_lines"], len(lines))):
        add(index, required=True)
    tail_start = max(0, len(lines) - parsed["tail_lines"])
    for index in range(tail_start, len(lines)):
        add(index, required=True)

    task_terms = context_compiler.terms(parsed["task"])
    ranked: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        score = _line_priority(line, task_terms)
        if score > 0:
            ranked.append((-score, index))
    ranked.sort()

    matched_lines = len(ranked)
    for _negative_score, index in ranked:
        window = [index]
        for distance in range(1, parsed["context_lines"] + 1):
            window.extend((index - distance, index + distance))
        for candidate in window:
            add(candidate, required=False)

    ordered = sorted(selected)
    records = [{"line": index + 1, "text": lines[index]} for index in ordered]
    return records, _omitted_ranges(len(lines), selected), matched_lines, selected_bytes


def reduce_request(root: Path, raw: Any) -> dict[str, Any]:
    parsed = _parse_request(raw)
    if not parsed["enabled"]:
        return {
            "decision": "BYPASS",
            "text": parsed["text"],
            "recovery_marker": None,
            "records": [],
            "omitted_ranges": [],
            "telemetry": {
                "decision": "BYPASS",
                "original_bytes": len(parsed["text_bytes"]),
                "original_lines": len(parsed["lines"]),
                "visible_bytes": len(parsed["text_bytes"]),
                "visible_lines": len(parsed["lines"]),
                "matched_lines": 0,
                "omitted_lines": 0,
                "truncated": False,
            },
        }

    if parsed["protected"]:
        raise ToolOutputError("PROTECTED_TOOL_OUTPUT_REDUCTION_FORBIDDEN")

    records, omitted_ranges, matched_lines, visible_bytes = _select_lines(parsed)
    try:
        marker, _deduplicated = context_fold.put(root, parsed["text"])
    except context_fold.FoldError as exc:
        raise ToolOutputError(f"RECOVERY_STORE:{exc}") from exc

    visible_lines = len(records)
    original_lines = len(parsed["lines"])
    omitted_lines = original_lines - visible_lines
    decision = "REDUCED" if omitted_lines > 0 else "UNCHANGED"
    return {
        "decision": decision,
        "text": None,
        "recovery_marker": marker,
        "records": records,
        "omitted_ranges": omitted_ranges,
        "telemetry": {
            "decision": decision,
            "original_bytes": len(parsed["text_bytes"]),
            "original_lines": original_lines,
            "visible_bytes": visible_bytes,
            "visible_lines": visible_lines,
            "matched_lines": matched_lines,
            "omitted_lines": omitted_lines,
            "truncated": omitted_lines > 0,
        },
    }


def _read_json(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise ToolOutputError("INPUT_INVALID") from exc


def _write_json_private(path: str, value: Any) -> None:
    target = Path(path)
    if target.is_symlink():
        raise ToolOutputError("OUTPUT_SYMLINK_FORBIDDEN")
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(target, flags, 0o600)
    except OSError as exc:
        raise ToolOutputError("OUTPUT_WRITE_FAILED") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ToolOutputError("OUTPUT_WRITE_FAILED")
        os.fchmod(fd, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise ToolOutputError("OUTPUT_WRITE_FAILED")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deterministic reversible line-oriented tool-output reduction"
    )
    parser.add_argument("--root", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = reduce_request(Path(args.root), _read_json(args.input))
        _write_json_private(args.output, result)
        telemetry = result["telemetry"]
        print(
            "TOOL_OUTPUT_REDUCER=PASS "
            f"decision={telemetry['decision']} "
            f"visible_lines={telemetry['visible_lines']} "
            f"omitted_lines={telemetry['omitted_lines']}"
        )
        return 0
    except ToolOutputError as exc:
        print(f"TOOL_OUTPUT_REDUCER=BLOCK reason={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
