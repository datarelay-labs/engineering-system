#!/usr/bin/env python3
"""Independent source-value oracle for actual Parser Studio Chromium captures.

Read-only T0 assessment: never validates actor identity or grants release/user-E2E PASS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

HEX64 = re.compile(r"[0-9a-f]{64}\Z")
MAX_JSON = 3 * 1024 * 1024


class EvidenceError(Exception):
    pass


def require(cond: bool, reason: str) -> None:
    if not cond:
        raise EvidenceError(reason)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_document(path: Path) -> tuple[dict, bytes]:
    require(path.is_file() and path.stat().st_size <= MAX_JSON, "MISSING_OR_OVERSIZED_DOCUMENT")
    raw = path.read_bytes()
    try:
        parsed = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise EvidenceError("INVALID_JSON") from exc
    require(type(parsed) is dict, "INVALID_DOCUMENT")
    return parsed, raw


def evidence_file(root: Path, name: str) -> bytes:
    require(name and "/" not in name and "\\" not in name and name not in (".", ".."), "UNSAFE_EVIDENCE_PATH")
    current = root / name
    require(not current.is_symlink(), "SYMLINK_EVIDENCE")
    require(current.resolve().is_relative_to(root.resolve()), "EVIDENCE_OUTSIDE_ROOT")
    require(current.is_file() and current.stat().st_size <= 10 * 1024 * 1024, "MISSING_EVIDENCE")
    return current.read_bytes()


def assess(mission: dict, capture: dict, draft: dict, *, frozen_sha256: str, raw_mission: bytes, root: Path) -> dict:
    require(bool(HEX64.fullmatch(frozen_sha256)), "INVALID_FROZEN_SHA")
    require(sha(raw_mission) == frozen_sha256, "ORACLE_TAMPER")
    require(mission.get("classification") == "SYNTHETIC_SECURITY_EVENTS_NO_CUSTOMER_DATA", "UNSAFE_UNSCOPED_MISSION")
    require(type(mission.get("input_records")) is list and 1 <= len(mission["input_records"]) <= 30,
            "UNBOUNDED_OR_MISSING_RECORDS")
    require(type(mission.get("independent_oracle")) is dict, "MISSING_FROZEN_ORACLE")
    require(capture.get("mission_sha256") == frozen_sha256 and draft.get("mission_sha256") == frozen_sha256,
            "OBSERVATION_MISSION_MISMATCH")
    source = "\n".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) for x in mission["input_records"])
    source_bytes = source.encode("utf-8")
    require(capture.get("input_sha256") == sha(source_bytes) and draft.get("source_sha256") == sha(source_bytes),
            "OBSERVED_SOURCE_MISMATCH")
    require(capture.get("html_status") == 200, "BROWSER_ENTRY_NOT_SUCCESS")
    require(not capture.get("errors") and not draft.get("errors"), "BROWSER_ERRORS")
    require(not capture.get("thirdparty"), "UNEXPECTED_THIRD_PARTY_EGRESS")
    require(type(capture.get("responses")) is list and len(capture["responses"]) <= 40, "BAD_CAPTURE_RESPONSES")
    preview_replies = [
        r for r in capture["responses"]
        if type(r) is dict and isinstance(r.get("url"), str)
        and r["url"].endswith("/api/v1/multipass/preview-batch")
    ]
    require(len(preview_replies) == 1 and preview_replies[0].get("status") == 200,
            "MISSING_OR_UNSUCCESSFUL_MULTIPASS_READBACK")
    reply = preview_replies[0]
    raw_response = reply.get("body")
    require(type(raw_response) is str and 0 < len(raw_response) <= 1024 * 1024, "BAD_PREVIEW_BODY")
    require(reply.get("sha256") == sha(raw_response.encode()), "CAPTURED_RESPONSE_TAMPER")
    try:
        parsed = json.loads(raw_response)
    except (ValueError, UnicodeError) as exc:
        raise EvidenceError("MALFORMED_PREVIEW") from exc

    expected = mission["input_records"]
    require(parsed.get("contract") == "parser-studio.multipass-source-batch-preview.v0.1", "PREVIEW_CONTRACT_MISMATCH")
    require(parsed.get("sourceSha256") == sha(source_bytes), "BACKEND_SOURCE_SHA_MISMATCH")
    require(parsed.get("sourceBytes") == len(source_bytes), "BACKEND_SOURCE_LENGTH_MISMATCH")
    require(parsed.get("nativeVerified") is False and parsed.get("targetVerified") is False,
            "FALSE_NATIVE_TARGET_VERIFICATION_CLAIM")
    require(parsed.get("eventCount") == len(expected), "SILENT_RECORD_LOSS")
    entries = parsed.get("events")
    require(type(entries) is list and len(entries) == len(expected), "EVENTS_LENGTH_MISMATCH")

    offset = 0
    mapped_field_values = 0
    for index, (original, event) in enumerate(zip(expected, entries, strict=True)):
        require(type(original) is dict and bool(original), "BAD_ORIGINAL_RECORD")
        require(type(event) is dict and event.get("index") == index, "WRONG_EVENT_ORDER")
        encoded = json.dumps(original, ensure_ascii=False, separators=(",", ":")).encode()
        start, end = event.get("startByte"), event.get("endByte")
        require(type(start) is int and type(end) is int and start == offset and end == offset + len(encoded),
                "WRONG_EVENT_BYTE_BOUNDARY")
        preview = event.get("preview")
        require(type(preview) is dict, "MISSING_EVENT_PREVIEW")
        require(preview.get("status") == "SOURCE_PARSED" and preview.get("syntax") == "JSON",
                "INCORRECT_PARSING_STATUS")
        require(preview.get("sourceSha256") == sha(encoded) and preview.get("sourceBytes") == len(encoded),
                "EVENT_SOURCE_IDENTITY_MISMATCH")
        fields = preview.get("fields")
        require(type(fields) is dict and fields == original, "SOURCE_FIELD_VALUE_MISMATCH")
        spans = preview.get("spans")
        require(type(spans) is list and len(spans) == len(original), "FIELD_BYTE_SPANS_MISSING")
        seen: set[str] = set()
        for span in spans:
            require(type(span) is dict, "MALFORMED_BYTE_SPAN")
            key = span.get("path")
            require(type(key) is str and key in original and key not in seen, "UNKNOWN_DUPLICATE_SPAN")
            a, b = span.get("startByte"), span.get("endByte")
            require(type(a) is int and type(b) is int and 0 <= a < b <= len(encoded), "INVALID_SPAN_OFFSET")
            value = json.dumps(original[key], ensure_ascii=False, separators=(",", ":")).encode()
            require(encoded[a:b] == value, "INCORRECT_SOURCE_BYTE_SPAN")
            seen.add(key)
            mapped_field_values += 1
        require(seen == set(original), "MISSING_EXTRACTED_SPANS")
        require(not preview.get("residuals") and not preview.get("issues"), "UNHANDLED_SOURCE_RESIDUAL")
        offset += len(encoded)
        if index + 1 < len(expected):
            require(source_bytes[offset:offset+1] == b"\n", "ORIGINAL_RECORD_SEPARATOR_CHANGED")
            offset += 1
    require(offset == len(source_bytes), "TRAILING_UNACCOUNTED_SOURCE")
    require(f"{len(expected)} of {len(expected)} log events parsed completely" in capture.get("summary", ""),
            "RENDERED_SUMMARY_CONTRADICTION")

    downloads = draft.get("artifacts")
    require(type(downloads) is list and len(downloads) == 2, "MISSING_PARSER_PAIR_DOWNLOAD")
    downloaded = set()
    for item in downloads:
        require(type(item) is dict and set(item) == {"filename", "bytes", "sha256", "path"},
                "BAD_DOWNLOAD_MANIFEST")
        name = item["filename"]
        require(type(name) is str and name in {"customized_demo_firewall", "customized_demo_firewall.conf"},
                "WRONG_DOWNLOAD_FILENAME")
        require(name not in downloaded, "DUPLICATE_DOWNLOAD")
        downloaded.add(name)
        data = evidence_file(root, item["path"])
        require(type(item["bytes"]) is int and item["bytes"] == len(data) and len(data) > 80,
                "INVALID_DOWNLOAD_SIZE")
        require(item["sha256"] == sha(data), "DOWNLOAD_HASH_MISMATCH")
        if name.endswith(".conf"):
            require(b"6801" in data and b"customized_demo_firewall" in data, "WRONG_LISTENER_CONFIG")
        else:
            require(b"customized_demo_firewall" in data or b"Base64.strict_decode64" in data,
                    "INVALID_PARSER_DRAFT")
    require("created successfully" in draft.get("generated_panel", "").lower(), "FALSE_GENERATION_SUCCESS")
    require("not been installed in a live Stellar system" in draft.get("generated_panel", ""),
            "MISSING_DRAFT_ONLY_DISCLOSURE")
    require(type(draft.get("network")) is list and all(
        type(x) is dict and 200 <= x.get("status", 0) < 400
        for x in draft["network"]), "BACKEND_HTTP_FAILURE")
    for fname in ("after-analysis.png", "review-fields.png", "draft-complete.png", "chromium-trace.zip"):
        require(len(evidence_file(root, fname)) > 1024, "MISSING_OR_EMPTY_REAL_BROWSER_PROOF")
    return {
        "kind": "ai-qa-parser-studio-outcome-oracle",
        "result": "INDEPENDENT_ORACLE_PASS",
        "authority": "T0_ADVISORY_ONLY",
        "human_equivalent_release_gate": False,
        "mission_sha256": frozen_sha256,
        "original_records": len(expected),
        "verified_extracted_values_and_spans": mapped_field_values,
        "downloaded_draft_files": sorted(downloaded),
        "trace_artifact": "chromium-trace.zip",
        "reason": "real observed files matched frozen source oracle; runtime/actor proof is separate",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Parser Studio source-preserving Chromium oracle")
    parser.add_argument("--mission", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--draft", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--expected-mission-sha256", required=True)
    args = parser.parse_args()
    try:
        mission, raw = read_document(args.mission)
        capture, _ = read_document(args.capture)
        draft, _ = read_document(args.draft)
        output = assess(mission, capture, draft, frozen_sha256=args.expected_mission_sha256,
                        raw_mission=raw, root=args.evidence_root)
        print(json.dumps(output, sort_keys=True))
        return 0
    except (OSError, EvidenceError, TypeError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, EvidenceError) else "INVALID_EVIDENCE"
        print(json.dumps({"kind": "ai-qa-parser-studio-outcome-oracle", "result": "BLOCK",
                          "authority": "T0_ADVISORY_ONLY", "human_equivalent_release_gate": False,
                          "reason": reason}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
