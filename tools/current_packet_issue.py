#!/usr/bin/env python3
"""Read-only projection of one explicitly delimited current Work Packet in an Issue.

The caller must separately authenticate the GitHub Issue identity/author and
bind the supplied digest to that authenticated read. A caller-supplied digest
does not establish provenance or permission. This helper never selects Issues,
executes work, changes Git, or confers mutation/release authority.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

from context_epoch import ContextError, analyze_packet, parse_packet, project_packet
from execution_profile import FENCE_RE

BEGIN = "<!-- ENGINEERING_CURRENT_PACKET_BEGIN -->"
END = "<!-- ENGINEERING_CURRENT_PACKET_END -->"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_ISSUE_BYTES = 1024 * 1024
MAX_PACKET_BYTES = 128 * 1024
MAX_PROJECTION_CHARS = 14000


def _marker_indices(source: str) -> tuple[list[str], list[int], list[int]]:
    lines = source.splitlines(keepends=True)
    begins: list[int] = []
    ends: list[int] = []
    fence: tuple[str, int] | None = None
    for index, line in enumerate(lines):
        raw = line.rstrip("\r\n")
        match = FENCE_RE.match(raw)
        if match:
            token = match.group(1)
            if fence is None:
                fence = (token[0], len(token))
            elif fence[0] == token[0] and len(token) >= fence[1]:
                fence = None
            continue
        if fence is not None:
            continue
        if raw == BEGIN:
            begins.append(index)
        elif raw == END:
            ends.append(index)
    return lines, begins, ends


def project_marked_issue(
    source: str,
    *,
    expected_source_sha256: str,
    expected_target_repo: str,
) -> str:
    """Produce bounded diagnostic context, never executable authority."""
    if not SHA256_RE.fullmatch(expected_source_sha256):
        raise ContextError("ISSUE_DIGEST_INVALID")
    if (
        not REPO_RE.fullmatch(expected_target_repo)
        or any(part in {".", ".."} for part in expected_target_repo.split("/"))
    ):
        raise ContextError("EXPECTED_TARGET_REPO_INVALID")
    if not isinstance(source, str) or len(source.encode("utf-8")) > MAX_ISSUE_BYTES:
        raise ContextError("ISSUE_SIZE_INVALID")
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    if digest != expected_source_sha256:
        raise ContextError("ISSUE_DIGEST_MISMATCH")

    lines, begins, ends = _marker_indices(source)
    if len(begins) != 1 or len(ends) != 1:
        raise ContextError("CURRENT_PACKET_MARKERS_AMBIGUOUS_OR_MISSING")
    if begins[0] >= ends[0]:
        raise ContextError("CURRENT_PACKET_MARKER_ORDER_INVALID")
    selected = "".join(lines[begins[0] + 1:ends[0]]).strip() + "\n"
    if len(selected.encode("utf-8")) > MAX_PACKET_BYTES:
        raise ContextError("CURRENT_PACKET_SIZE_INVALID")
    packet = parse_packet(selected)
    lint = analyze_packet(packet, expected_target_repo=expected_target_repo)
    if lint["status"] != "PASS":
        reasons = lint["blocking"] or lint["warnings"]
        raise ContextError("CURRENT_PACKET_INVALID:" + ",".join(reasons)[:600])
    if packet.metadata.get("STATUS") != "ACTIVE":
        raise ContextError("CURRENT_PACKET_NOT_ACTIVE")

    prefix = (
        "ISSUE_PACKET_SOURCE_SHA256=" + digest + "\n"
        + "ISSUE_PACKET_SELECTION=EXPLICIT_MARKERS\n"
        + "ISSUE_PACKET_PROVENANCE=CALLER_SUPPLIED_NOT_ATTESTED\n"
        + "MUTATION_AUTHORITY=NO\n\n"
    )
    projection = project_packet(
        packet, projection_char_cap=MAX_PROJECTION_CHARS - len(prefix)
    )
    output = prefix + projection
    if len(output) > MAX_PROJECTION_CHARS:
        raise ContextError("CURRENT_PACKET_PROJECTION_TOO_LARGE")
    return output



def read_exact_source(path: str) -> str:
    """Preserve GitHub body bytes: text-mode reads normalize CRLF and break SHA."""
    if path == "-":
        raw = sys.stdin.buffer.read(MAX_ISSUE_BYTES + 1)
    else:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_ISSUE_BYTES + 1)
    if len(raw) > MAX_ISSUE_BYTES:
        raise ContextError("ISSUE_SIZE_INVALID")
    return raw.decode("utf-8")

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--body-file", required=True, help="Authenticated Issue body file or -")
    parser.add_argument("--expect-body-sha256", required=True)
    parser.add_argument("--expect-target-repo", required=True)
    args = parser.parse_args(argv)
    try:
        body = read_exact_source(args.body_file)
        print(project_marked_issue(
            body,
            expected_source_sha256=args.expect_body_sha256,
            expected_target_repo=args.expect_target_repo,
        ), end="")
        return 0
    except (ContextError, OSError, UnicodeError, ValueError) as exc:
        print(f"ISSUE_PACKET=BLOCK reason={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
