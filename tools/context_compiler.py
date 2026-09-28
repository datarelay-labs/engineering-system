#!/usr/bin/env python3
"""Deterministic protected-first context compiler.

The compiler consumes already-authorized structured context.  It never decides
authority, invokes a model/provider, estimates billing tokens, or reads agent
transcripts.  Protected engineering state is selected before optional context
and is never truncated to satisfy a budget.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_BLOCKS = 256
MAX_TASK_BYTES = 8192
MAX_BLOCK_TEXT_BYTES = 131072
MAX_REFERENCE_BYTES = 2048
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SAFE_KIND_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
TERM_RE = re.compile(r"[\w][\w.-]{1,}", re.UNICODE)
CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
TOKEN_SEPARATOR_RE = re.compile(r"[_/:=]+")
RESERVED_CONTEXT_MARKERS = ("<<<CONTEXT_BLOCK", "<<<END_CONTEXT_BLOCK>>>")
ALLOWED_KINDS = frozenset({
    "code", "config", "diff", "docs", "git", "json",
    "log", "repo_map", "test", "tool", "work_packet",
})
REQUEST_FIELDS = frozenset({"task", "blocks"})
BLOCK_FIELDS = frozenset({
    "id", "kind", "text", "reference", "protected", "protection_class", "priority",
})
PROTECTED_CLASSES = frozenset({
    "acceptance_criteria",
    "blockers",
    "completion_criteria",
    "constraints",
    "current_state",
    "evidence_reference",
    "goal",
    "next_action",
    "repository_identity",
    "security_constraint",
    "unresolved_failure",
})


class CompilerError(ValueError):
    pass


@dataclass(frozen=True)
class ContextBlock:
    block_id: str
    kind: str
    text: str
    reference: str | None
    protected: bool
    protection_class: str | None
    priority: int


def utf8_len(value: str) -> int:
    return len(value.encode("utf-8"))


def safe_text(value: Any, *, field: str, max_bytes: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise CompilerError(f"INVALID_{field.upper()}")
    if not allow_empty and not value.strip():
        raise CompilerError(f"INVALID_{field.upper()}")
    if utf8_len(value) > max_bytes:
        raise CompilerError(f"{field.upper()}_TOO_LARGE")
    return value


def parse_block(raw: Any) -> ContextBlock:
    if not isinstance(raw, dict):
        raise CompilerError("INVALID_BLOCK")
    unknown = sorted(set(raw) - BLOCK_FIELDS)
    if unknown:
        raise CompilerError("UNKNOWN_BLOCK_FIELD:" + ",".join(unknown))
    block_id = safe_text(raw.get("id"), field="block_id", max_bytes=128)
    if SAFE_ID_RE.fullmatch(block_id) is None:
        raise CompilerError("INVALID_BLOCK_ID")
    kind = safe_text(raw.get("kind"), field="kind", max_bytes=64).casefold()
    if SAFE_KIND_RE.fullmatch(kind) is None:
        raise CompilerError("INVALID_KIND")
    if kind not in ALLOWED_KINDS:
        raise CompilerError("KIND_NOT_ALLOWED")
    text = safe_text(
        raw.get("text"), field="block_text", max_bytes=MAX_BLOCK_TEXT_BYTES,
        allow_empty=False,
    )
    if any(marker in text for marker in RESERVED_CONTEXT_MARKERS):
        raise CompilerError("RESERVED_CONTEXT_MARKER_IN_TEXT")
    reference_raw = raw.get("reference")
    reference = None
    if reference_raw is not None:
        reference = safe_text(
            reference_raw, field="reference", max_bytes=MAX_REFERENCE_BYTES,
            allow_empty=False,
        )
    protected = raw.get("protected", False)
    if not isinstance(protected, bool):
        raise CompilerError("INVALID_PROTECTED_FLAG")
    protection_class = raw.get("protection_class")
    if protected:
        if not isinstance(protection_class, str) or protection_class not in PROTECTED_CLASSES:
            raise CompilerError("INVALID_PROTECTION_CLASS")
    elif protection_class is not None:
        raise CompilerError("OPTIONAL_PROTECTION_CLASS_FORBIDDEN")
    if kind == "work_packet" and not protected:
        raise CompilerError("WORK_PACKET_KIND_REQUIRES_PROTECTION")
    priority = raw.get("priority", 0)
    if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 100:
        raise CompilerError("INVALID_PRIORITY")
    return ContextBlock(
        block_id=block_id,
        kind=kind,
        text=text,
        reference=reference,
        protected=protected,
        protection_class=protection_class,
        priority=priority,
    )


def parse_request(raw: Any) -> tuple[str, list[ContextBlock]]:
    if not isinstance(raw, dict):
        raise CompilerError("INVALID_REQUEST")
    unknown = sorted(set(raw) - REQUEST_FIELDS)
    if unknown:
        raise CompilerError("UNKNOWN_REQUEST_FIELD:" + ",".join(unknown))
    task = safe_text(raw.get("task"), field="task", max_bytes=MAX_TASK_BYTES)
    blocks_raw = raw.get("blocks")
    if not isinstance(blocks_raw, list) or not blocks_raw or len(blocks_raw) > MAX_BLOCKS:
        raise CompilerError("INVALID_BLOCKS")
    blocks = [parse_block(item) for item in blocks_raw]
    ids = [item.block_id for item in blocks]
    if len(ids) != len(set(ids)):
        raise CompilerError("DUPLICATE_BLOCK_ID")
    return task, blocks


def terms(value: str) -> frozenset[str]:
    # Provider-neutral lexical preprocessing: split common code/JSON/log
    # separators and camelCase before extracting Unicode-aware terms.
    normalized = CAMEL_BOUNDARY_RE.sub(" ", value)
    normalized = TOKEN_SEPARATOR_RE.sub(" ", normalized)
    return frozenset(match.group(0).casefold() for match in TERM_RE.finditer(normalized))


def render_block(block: ContextBlock) -> str:
    reference = block.reference if block.reference is not None else "-"
    protection = block.protection_class if block.protection_class is not None else "-"
    return (
        "<<<CONTEXT_BLOCK "
        f"id={json.dumps(block.block_id, ensure_ascii=False)} "
        f"kind={json.dumps(block.kind, ensure_ascii=False)} "
        f"ref={json.dumps(reference, ensure_ascii=False)} "
        f"protected={'true' if block.protected else 'false'} "
        f"protection_class={json.dumps(protection, ensure_ascii=False)}>>>\n"
        f"{block.text}\n"
        "<<<END_CONTEXT_BLOCK>>>\n"
    )


def rank_optional(task: str, block: ContextBlock) -> tuple[int, int, str, str]:
    overlap = len(terms(task) & terms(block.text))
    # Negative values make higher relevance/priority sort first.  Kind and ID
    # provide deterministic provider-neutral tie breaking.
    return (-overlap, -block.priority, block.kind, block.block_id)


def _counts(blocks: list[ContextBlock]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for block in blocks:
        counts[block.kind] = counts.get(block.kind, 0) + 1
    return dict(sorted(counts.items()))


def compile_context(task: str, blocks: list[ContextBlock], budget_bytes: int) -> dict[str, Any]:
    if isinstance(budget_bytes, bool) or not isinstance(budget_bytes, int) or budget_bytes <= 0:
        raise CompilerError("INVALID_BUDGET")

    rendered = {block.block_id: render_block(block) for block in blocks}
    sizes = {block_id: utf8_len(value) for block_id, value in rendered.items()}
    protected = [block for block in blocks if block.protected]
    optional = [block for block in blocks if not block.protected]
    protected_bytes = sum(sizes[block.block_id] for block in protected)
    if protected_bytes > budget_bytes:
        raise CompilerError(
            f"PROTECTED_BUDGET_EXCEEDED:{protected_bytes}>{budget_bytes}"
        )

    selected_ids = {block.block_id for block in protected}
    used = protected_bytes
    for block in sorted(optional, key=lambda item: rank_optional(task, item)):
        size = sizes[block.block_id]
        if used + size <= budget_bytes:
            selected_ids.add(block.block_id)
            used += size

    selected = [block for block in blocks if block.block_id in selected_ids]
    dropped = [block for block in optional if block.block_id not in selected_ids]
    context = "".join(rendered[block.block_id] for block in selected)
    kept_bytes = utf8_len(context)
    if kept_bytes != used or kept_bytes > budget_bytes:
        raise CompilerError("INTERNAL_BUDGET_VIOLATION")

    original_context = "".join(rendered[block.block_id] for block in blocks)
    original_bytes = utf8_len(original_context)
    optional_kept = [block for block in selected if not block.protected]
    reduction_ratio = 0.0 if original_bytes == 0 else 1.0 - (kept_bytes / original_bytes)
    reason = "WITHIN_BUDGET" if not dropped else "OPTIONAL_DROPPED_BUDGET"
    telemetry = {
        "budget_bytes": budget_bytes,
        "compiler_decision": "COMPILED",
        "reason": reason,
        "original_chars": len(original_context),
        "original_bytes": original_bytes,
        "kept_chars": len(context),
        "kept_bytes": kept_bytes,
        "protected_block_count": len(protected),
        "optional_kept_count": len(optional_kept),
        "optional_dropped_count": len(dropped),
        "optional_kept_by_kind": _counts(optional_kept),
        "optional_dropped_by_kind": _counts(dropped),
        "reduction_ratio": round(reduction_ratio, 6),
    }
    return {
        "version": 1,
        "decision": "COMPILED",
        "context": context,
        "selected_blocks": [
            {
                "id": block.block_id,
                "kind": block.kind,
                "protected": block.protected,
                "protection_class": block.protection_class,
                "reference": block.reference,
            }
            for block in selected
        ],
        "telemetry": telemetry,
    }


def read_json(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str, value: Any) -> None:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    Path(path).write_text(payload, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compile bounded engineering context")
    sub = parser.add_subparsers(dest="command", required=True)
    compile_cmd = sub.add_parser("compile")
    compile_cmd.add_argument("--input", required=True)
    compile_cmd.add_argument("--budget-bytes", required=True, type=int)
    compile_cmd.add_argument("--telemetry-file")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "compile":
            task, blocks = parse_request(read_json(args.input))
            result = compile_context(task, blocks, args.budget_bytes)
            if args.telemetry_file:
                write_json(args.telemetry_file, result["telemetry"])
            json.dump(result, sys.stdout, sort_keys=True, ensure_ascii=False)
            sys.stdout.write("\n")
            return 0
    except (CompilerError, json.JSONDecodeError, OSError) as exc:
        print(f"CONTEXT_COMPILER_ERROR={exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
