#!/usr/bin/env python3
"""Forward one native Cursor hook payload to the bound lane adapter.

A missing descriptor, a missing or swapped adapter, invalid JSON, or an
adapter BLOCK becomes a blocking hook result. The payload is not printed.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import sys
from pathlib import Path


def _deny(payload: object) -> dict:
    name = payload.get("hook_event_name") if isinstance(payload, dict) else None
    if name in {"preToolUse", "beforeShellExecution"}:
        return {"permission": "deny"}
    if name == "beforeSubmitPrompt":
        return {"continue": False}
    return {"permission": "deny", "continue": False}


def _emit(response: dict, code: int) -> int:
    json.dump(response, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return code


def _bound_digest(descriptor_path: Path) -> str | None:
    try:
        document = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    digest = document.get("adapter_digest") if isinstance(document, dict) else None
    if not isinstance(digest, str) or len(digest) != 64:
        return None
    return digest


def _adapter_matches(module_path: Path, expected: str) -> bool:
    try:
        actual = hashlib.sha256(module_path.read_bytes()).hexdigest()
    except OSError:
        return False
    return hmac.compare_digest(actual, expected)


def _load_adapter(module_path: Path):
    spec = importlib.util.spec_from_file_location("persistent_benchmark_telemetry", module_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else None
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, dict):
        return _emit(_deny(None), 1)
    module_path = os.environ.get("ES_BENCHMARK_TELEMETRY_MODULE", "")
    descriptor = os.environ.get("ES_BENCHMARK_LANE_DESCRIPTOR", "")
    plugin_root = os.environ.get("ES_BENCHMARK_PLUGIN_ROOT", "") or str(Path(__file__).resolve().parents[1])
    if not module_path or not descriptor:
        return _emit(_deny(payload), 1)
    module_file = Path(module_path)
    expected = _bound_digest(Path(descriptor))
    if expected is None or not _adapter_matches(module_file, expected):
        return _emit(_deny(payload), 1)
    try:
        module = _load_adapter(module_file)
        if module is None:
            return _emit(_deny(payload), 1)
        result = module.ingest_hook(
            payload,
            descriptor_path=Path(descriptor),
            plugin_root=Path(plugin_root),
        )
    except Exception:
        return _emit(_deny(payload), 1)
    if not isinstance(result, dict) or result.get("blocked") is not False:
        return _emit(_deny(payload), 1)
    response = result.get("hook_response")
    if not isinstance(response, dict):
        return _emit(_deny(payload), 1)
    return _emit(response, 0)


if __name__ == "__main__":
    sys.exit(main())
