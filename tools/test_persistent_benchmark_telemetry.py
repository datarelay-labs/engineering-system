#!/usr/bin/env python3
"""Native benchmark hook receipts and canonical telemetry finalization."""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "persistent_benchmark_telemetry.py"
_SPEC = importlib.util.spec_from_file_location("persistent_benchmark_telemetry", TOOL)
assert _SPEC and _SPEC.loader
capture = importlib.util.module_from_spec(_SPEC)
sys.modules["persistent_benchmark_telemetry"] = capture
_SPEC.loader.exec_module(capture)

PROFILE = {
    "provider": "cursor",
    "model": "gpt-5.6-sol-medium",
    "reasoning": "medium",
    "toolset": "write-shell-allowlist",
}
EXPECTED_EFFECTIVE_SANDBOX = "disabled"
CURSOR_VERSION = "2026.09.26-dd393fe"
RUN_ID = "a" * 32
SECRET = "super-secret-prompt"
TOOL_SECRET = "tool-input-secret"
OUTPUT_SECRET = "tool-output-secret"
PATH_SECRET = "/home/aella/secret-path"
EMAIL_SECRET = "user@example.com"


def _fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def _prepare(root: Path, *, lane: str = "CONTROL", run_id: str = RUN_ID) -> dict:
    task = root / "task"
    task.mkdir()
    (task / "SOURCE").write_text("frozen\n", encoding="utf-8")
    return capture.prepare_lane(
        state_dir=root / "state",
        task_root=task,
        snapshot_dir=root / "plugin",
        run_id=run_id,
        case_id="BENCH-BUG-001",
        lane=lane,
        profile=PROFILE,
        cursor_version=CURSOR_VERSION,
        expected_effective_sandbox=EXPECTED_EFFECTIVE_SANDBOX,
    )


def _session(conversation: str = "conv-1", **extra: object) -> dict:
    payload = {
        "hook_event_name": "sessionStart",
        "conversation_id": conversation,
        "session_id": conversation,
        "generation_id": "gen-1",
        "cursor_version": CURSOR_VERSION,
        "model": PROFILE["model"],
        "model_params": [{"id": "effort", "value": PROFILE["reasoning"]}],
        "prompt": SECRET,
        "workspace_roots": [PATH_SECRET],
        "user_email": EMAIL_SECRET,
        "transcript_path": PATH_SECRET + "/transcript.jsonl",
    }
    payload.update(extra)
    return payload


def _shell(sandbox: bool = False) -> dict:
    return {
        "hook_event_name": "beforeShellExecution",
        "conversation_id": "conv-1",
        "cursor_version": CURSOR_VERSION,
        "sandbox": sandbox,
        "command": "echo-secret-command",
    }


def _stored_text(root: Path) -> str:
    chunks: list[str] = []
    for path in (root / "state").rglob("*"):
        if path.is_file() and not path.name.endswith(".hmac-key"):
            chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def test_plugin_is_hook_only() -> None:
    source = capture.PLUGIN_ROOT
    if (ROOT / ".cursor" / "hooks.json").exists():
        _fail("project hooks.json is present")
    manifest = json.loads((source / ".cursor-plugin" / "plugin.json").read_text(encoding="utf-8"))
    hooks = json.loads((source / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    if manifest["name"] != capture.PLUGIN_NAME or set(hooks["hooks"]) != capture.NATIVE_EVENTS:
        _fail("plugin is not the hook-only native surface")
    for event, entries in hooks["hooks"].items():
        command = entries[0]["command"]
        if "record.py" not in command or ".cursor/hooks.json" in command or "~/.cursor" in command:
            _fail("hook command leaves the plugin recorder")
        if event in capture.BLOCKING_HOOKS and entries[0].get("failClosed") is not True:
            _fail(f"{event} does not fail closed")
        if event == "sessionStart" and entries[0].get("failClosed"):
            _fail("sessionStart claimed it can block")
    first = capture.plugin_digest(source)
    if first != capture.plugin_digest(source) or len(first) != 64:
        _fail("plugin digest is unstable")
    text = TOOL.read_text(encoding="utf-8")
    if "subprocess" in text or "os.system" in text or "Popen(" in text:
        _fail("adapter grew a worker")
    if "def finalize_lane" not in text or "efficiency_telemetry.build_record" not in text or "efficiency_telemetry.write_record" not in text:
        _fail("finalization left the canonical telemetry API")


def test_prepare_stays_outside_task_tree() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        task = root / "task"
        if list(task.rglob("*")) != [task / "SOURCE"]:
            _fail("historical task tree changed")
        if (task / "SOURCE").read_text(encoding="utf-8") != "frozen\n":
            _fail("historical task source changed")
        descriptor = json.loads(Path(prepared["descriptor_path"]).read_text(encoding="utf-8"))
        identity = capture.benchmark_execution.FROZEN_CASE_IDENTITIES["BENCH-BUG-001"]
        if descriptor["repository"] != identity["repository"]:
            _fail("descriptor repository drifted")
        if descriptor["task_source_head"] != identity["source_commit"]:
            _fail("descriptor task source drifted")
        if descriptor["system_head"] != capture.benchmark_fixture.CONTROL_HEAD:
            _fail("descriptor system head drifted")
        if descriptor["profile"] != PROFILE or descriptor["cursor_version"] != CURSOR_VERSION:
            _fail("descriptor profile binding drifted")
        if descriptor["expected_effective_sandbox"] != "disabled":
            _fail("effective sandbox was equated with the launch request")
        if "sandbox" in descriptor or descriptor["profile"]["toolset"] != "write-shell-allowlist":
            _fail("descriptor toolset or requested sandbox drifted")
        if descriptor["plugin_digest"] != prepared["plugin_digest"]:
            _fail("descriptor plugin digest drifted")
        if descriptor["adapter_digest"] != capture.adapter_digest():
            _fail("descriptor adapter digest drifted")
        if "/" not in descriptor["repository"]:
            _fail("canonical repository identity was rejected")
        for relative in capture.PLUGIN_FILES:
            if not (root / "plugin" / relative).is_file():
                _fail(f"plugin snapshot missed {relative}")
        try:
            capture.stage_plugin(root / "plugin", task_root=task)
        except capture.CaptureError as exc:
            if exc.code != "PLUGIN_SNAPSHOT_EXISTS":
                _fail(f"existing snapshot returned {exc.code}")
        else:
            _fail("pre-existing snapshot root was reused")
        if PATH_SECRET in Path(prepared["descriptor_path"]).read_text(encoding="utf-8"):
            _fail("descriptor retained a path")
        if prepared["execute_worker"] is not False or prepared["argv"][:1] != ["--plugin-dir"]:
            _fail("prepare grew a worker launch")
        try:
            capture.prepare_lane(
                state_dir=task / "inside",
                task_root=task,
                snapshot_dir=root / "other-plugin",
                run_id=RUN_ID,
                case_id="BENCH-BUG-001",
                lane="CONTROL",
                profile=PROFILE,
                cursor_version=CURSOR_VERSION,
                expected_effective_sandbox=EXPECTED_EFFECTIVE_SANDBOX,
            )
        except capture.CaptureError as exc:
            if exc.code != "TASK_TREE_MUTATION":
                _fail(f"task-tree write returned {exc.code}")
        else:
            _fail("descriptor inside the task tree was accepted")


def test_handshake_and_gate() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        before = capture.gate_launch(descriptor)
        if before != {"admission": "BLOCK", "reason": "HANDSHAKE_MISSING", "execute_worker": False}:
            _fail(f"launch before handshake was {before}")
        started = capture.ingest_hook(_session(), descriptor_path=descriptor, plugin_root=plugin)
        if started["reason"] != "INCOMPLETE_LIFECYCLE" or started["hook_response"] != {}:
            _fail(f"sessionStart result was {started}")
        receipt = json.loads((root / "state" / f"{RUN_ID}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        handshake = receipt["handshake"]
        if handshake != {
            "cursor_version": CURSOR_VERSION,
            "conversation_id": "conv-1",
            "session_id": "conv-1",
            "reasoning": "medium",
        }:
            _fail(f"handshake was {handshake}")
        if receipt["plugin_digest"] != prepared["plugin_digest"]:
            _fail("receipt left the plugin digest")
        shell = capture.ingest_hook(_shell(), descriptor_path=descriptor, plugin_root=plugin)
        if shell["blocked"] is not False or shell["hook_response"] != {"permission": "allow"}:
            _fail(f"sandbox observation was {shell}")
        if "echo-secret-command" in _stored_text(root):
            _fail("shell command text was retained")
        stopped = capture.ingest_hook(
            {
                "hook_event_name": "stop",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "status": "completed",
                "prompt": SECRET,
            },
            descriptor_path=descriptor,
            plugin_root=plugin,
        )
        if stopped["status"] != "READY" or stopped["reason"] != "HANDSHAKE_BOUND":
            _fail(f"completed lifecycle was {stopped}")
        admitted = capture.gate_launch(descriptor)
        if admitted["admission"] != "READY" or admitted["execute_worker"] is not False:
            _fail(f"launch gate was {admitted}")
        if (root / "state" / f"{RUN_ID}-CONTROL.hmac-key").exists():
            _fail("finalization left the HMAC key")
        if "efficiency-telemetry" in _stored_text(root):
            _fail("slice A wrote a canonical telemetry record")


def test_fail_closed_evidence() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        version = capture.ingest_hook(
            _session(cursor_version="2026.09.26-aaaaaaa"),
            descriptor_path=descriptor,
            plugin_root=plugin,
        )
        if version["reason"] != "VERSION_MISMATCH":
            _fail(f"version drift returned {version['reason']}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        plugin = Path(prepared["plugin_dir"])
        mutated = plugin / "hooks" / "record.py"
        mutated.write_bytes(mutated.read_bytes() + b"\n")
        plugin_result = capture.ingest_hook(
            _session(),
            descriptor_path=Path(prepared["descriptor_path"]),
            plugin_root=plugin,
        )
        if plugin_result["reason"] != "PLUGIN_DIGEST_MISMATCH":
            _fail(f"plugin drift returned {plugin_result['reason']}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        capture.ingest_hook(_session(), descriptor_path=descriptor, plugin_root=plugin)
        foreign = capture.ingest_hook(
            {
                "hook_event_name": "stop",
                "conversation_id": "conv-2",
                "cursor_version": CURSOR_VERSION,
                "status": "completed",
            },
            descriptor_path=descriptor,
            plugin_root=plugin,
        )
        if foreign["reason"] != "SESSION_MISMATCH":
            _fail(f"foreign session returned {foreign['reason']}")
        aborted = capture.ingest_hook(
            {
                "hook_event_name": "sessionEnd",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "reason": "aborted",
            },
            descriptor_path=descriptor,
            plugin_root=plugin,
        )
        if aborted["reason"] != "SESSION_MISMATCH":
            _fail("blocked lane accepted another session")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, lane="CANDIDATE", run_id="b" * 32)
        document = json.loads(Path(prepared["descriptor_path"]).read_text(encoding="utf-8"))
        if document["lane"] != "CANDIDATE" or document["system_head"] != capture.benchmark_fixture.CANDIDATE_HEAD:
            _fail("candidate lane binding drifted")
        swapped = Path(prepared["descriptor_path"]).read_bytes()
        control_name = capture.descriptor_path(root / "state", "b" * 32, "CONTROL")
        control_name.write_bytes(swapped)
        swapped_gate = capture.gate_launch(control_name)
        if swapped_gate["reason"] != "DESCRIPTOR_MUTATED":
            _fail(f"lane file swap returned {swapped_gate['reason']}")


def test_native_receipt_drops_content_and_duplicates() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        capture.ingest_hook(_session(), descriptor_path=descriptor, plugin_root=plugin)
        tool = {
            "hook_event_name": "preToolUse",
            "conversation_id": "conv-1",
            "cursor_version": CURSOR_VERSION,
            "generation_id": "gen-1",
            "tool_use_id": "tool-1",
            "tool_name": "Read",
            "tool_input": {"path": PATH_SECRET, "secret": TOOL_SECRET},
            "cwd": PATH_SECRET,
        }
        capture.ingest_hook(tool, descriptor_path=descriptor, plugin_root=plugin)
        capture.ingest_hook(tool, descriptor_path=descriptor, plugin_root=plugin)
        posted = dict(tool)
        posted["hook_event_name"] = "postToolUse"
        posted["tool_output"] = OUTPUT_SECRET
        capture.ingest_hook(posted, descriptor_path=descriptor, plugin_root=plugin)
        capture.ingest_hook(
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "generation_id": "gen-2",
                "prompt": SECRET,
            },
            descriptor_path=descriptor,
            plugin_root=plugin,
        )
        stored = _stored_text(root)
        for needle in (SECRET, TOOL_SECRET, OUTPUT_SECRET, PATH_SECRET, EMAIL_SECRET, "transcript"):
            if needle in stored:
                _fail(f"receipt retained {needle}")
        receipt = json.loads((root / "state" / f"{RUN_ID}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        names = [item["hook_event_name"] for item in receipt["events"]]
        if names != ["sessionStart", "preToolUse", "postToolUse", "beforeSubmitPrompt"]:
            _fail(f"events were {names}")
        if receipt["events"][1]["tool_category"] != "read" or "tool_name" in json.dumps(receipt):
            _fail("tool name was retained")
        if "fingerprints" in receipt:
            _fail("durable receipt kept fingerprints")
        encoded = json.dumps({"path": PATH_SECRET, "secret": TOOL_SECRET}, sort_keys=True, separators=(",", ":")).encode()
        key = (root / "state" / f"{RUN_ID}-CONTROL.hmac-key").read_bytes()
        digest = hmac.new(key, b"read\0" + encoded, hashlib.sha256).hexdigest()
        plain = hashlib.sha256(b"read\0" + encoded).hexdigest()
        fingerprints = json.loads((root / "state" / f"{RUN_ID}-CONTROL.fingerprints.json").read_text(encoding="utf-8"))
        if fingerprints["fingerprints"].get("tool-1") != digest or digest == plain:
            _fail("fingerprint was not a run-local HMAC")
        marker = capture.ingest_hook(
            {"hook_event_name": "ES-EVENT", "cursor_version": CURSOR_VERSION, "line": "ES-PTY/es-pty-v1"},
            descriptor_path=descriptor,
            plugin_root=plugin,
        )
        if marker["reason"] != "SYNTHETIC_MARKER":
            _fail(f"synthetic marker returned {marker['reason']}")


def _run_recorder(root: Path, prepared: dict, payload: str, *, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    recorder_env = {
        **os.environ,
        "ES_BENCHMARK_LANE_DESCRIPTOR": prepared["env"]["ES_BENCHMARK_LANE_DESCRIPTOR"],
        "ES_BENCHMARK_TELEMETRY_MODULE": prepared["env"]["ES_BENCHMARK_TELEMETRY_MODULE"],
        "ES_BENCHMARK_PLUGIN_ROOT": prepared["env"]["ES_BENCHMARK_PLUGIN_ROOT"],
    }
    if env is not None:
        recorder_env = env
    return subprocess.run(
        [sys.executable, str(capture.PLUGIN_ROOT / "hooks" / "record.py")],
        input=payload,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=recorder_env,
        cwd=root,
    )


def test_repository_identity_is_field_aware() -> None:
    capture._content_free({"repository": "datarelay-labs/engineering-system"})
    try:
        capture._content_free({"conversation_id": "datarelay-labs/engineering-system"})
    except capture.CaptureError as exc:
        if exc.code != "PROHIBITED_CONTENT":
            _fail(f"non-repository slash returned {exc.code}")
    else:
        _fail("slash in a non-repository field was accepted")


def test_stop_and_session_end_status_are_separate() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        capture.ingest_hook(_session(), descriptor_path=descriptor, plugin_root=plugin)
        missing_status = capture.ingest_hook(
            {
                "hook_event_name": "stop",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "reason": "completed",
            },
            descriptor_path=descriptor,
            plugin_root=plugin,
        )
        if missing_status["reason"] != "INCOMPLETE_LIFECYCLE" or missing_status["blocked"] is not True:
            _fail(f"stop without status returned {missing_status}")
        blocked = json.loads((root / "state" / f"{RUN_ID}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        if blocked["block"] != "INCOMPLETE_LIFECYCLE":
            _fail("blocked receipt was not retained")
        if (root / "state" / f"{RUN_ID}-CONTROL.hmac-key").exists():
            _fail("blocked lane left the HMAC key")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="c" * 32)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        capture.ingest_hook(_session(), descriptor_path=descriptor, plugin_root=plugin)
        capture.ingest_hook(_shell(), descriptor_path=descriptor, plugin_root=plugin)
        stopped = capture.ingest_hook(
            {
                "hook_event_name": "stop",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "status": "completed",
                "reason": "aborted",
            },
            descriptor_path=descriptor,
            plugin_root=plugin,
        )
        if stopped["status"] != "READY":
            _fail(f"stop status completed was {stopped}")
        if (root / "state" / f"{'c' * 32}-CONTROL.hmac-key").exists():
            _fail("stop finalization left the HMAC key")
        closed = capture.ingest_hook(
            {
                "hook_event_name": "sessionEnd",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "reason": "window_close",
            },
            descriptor_path=descriptor,
            plugin_root=plugin,
        )
        if closed["status"] != "READY" or closed["blocked"] is not False:
            _fail(f"window_close after completion was {closed}")
        user_close = capture.ingest_hook(
            {
                "hook_event_name": "sessionEnd",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "reason": "user_close",
            },
            descriptor_path=descriptor,
            plugin_root=plugin,
        )
        if user_close["status"] != "READY":
            _fail(f"user_close after completion was {user_close}")
        final = json.loads((root / "state" / f"{'c' * 32}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        if final["lifecycle"] != "COMPLETE" or final["block"] is not None:
            _fail("normal session close corrupted the completed receipt")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="d" * 32)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        capture.ingest_hook(_session(), descriptor_path=descriptor, plugin_root=plugin)
        missing_reason = capture.ingest_hook(
            {
                "hook_event_name": "sessionEnd",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "status": "completed",
            },
            descriptor_path=descriptor,
            plugin_root=plugin,
        )
        if missing_reason["reason"] != "INCOMPLETE_LIFECYCLE":
            _fail(f"sessionEnd without reason returned {missing_reason['reason']}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="e" * 32)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        capture.ingest_hook(_session(), descriptor_path=descriptor, plugin_root=plugin)
        capture.ingest_hook(_shell(), descriptor_path=descriptor, plugin_root=plugin)
        ended = capture.ingest_hook(
            {
                "hook_event_name": "sessionEnd",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "reason": "completed",
                "status": "aborted",
            },
            descriptor_path=descriptor,
            plugin_root=plugin,
        )
        if ended["status"] != "READY":
            _fail(f"sessionEnd reason completed was {ended}")
        if (root / "state" / f"{'e' * 32}-CONTROL.fingerprints.json").exists():
            _fail("sessionEnd finalization left fingerprint state")


def _file_bytes(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_duplicate_prepare_leaves_existing_lane_unchanged() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        before = _file_bytes(root)
        try:
            capture.prepare_lane(
                state_dir=root / "state",
                task_root=root / "task",
                snapshot_dir=root / "other-plugin",
                run_id=RUN_ID,
                case_id="BENCH-BUG-001",
                lane="CONTROL",
                profile=PROFILE,
                cursor_version=CURSOR_VERSION,
                expected_effective_sandbox=EXPECTED_EFFECTIVE_SANDBOX,
            )
        except capture.CaptureError as exc:
            if exc.code != "DESCRIPTOR_EXISTS":
                _fail(f"duplicate prepare returned {exc.code}")
        else:
            _fail("duplicate prepare replaced the lane")
        if _file_bytes(root) != before:
            _fail("duplicate prepare changed existing lane bytes")
        if (root / "other-plugin").exists():
            _fail("duplicate prepare left a new plugin snapshot")


def test_profile_and_sandbox_parity() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        reasoning = capture.ingest_hook(
            _session(model_params=[{"id": "reasoning", "value": "high"}]),
            descriptor_path=descriptor,
            plugin_root=plugin,
        )
        if reasoning["reason"] != "PROFILE_MISMATCH":
            _fail(f"reasoning mismatch returned {reasoning['reason']}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="7" * 32)
        missing = capture.ingest_hook(
            _session(model_params=[]),
            descriptor_path=Path(prepared["descriptor_path"]),
            plugin_root=Path(prepared["plugin_dir"]),
        )
        if missing["reason"] != "MISSING_PROFILE_EVIDENCE":
            _fail(f"missing reasoning returned {missing['reason']}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="6" * 32)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        native = capture.ingest_hook(
            _session(
                model_params=[
                    {"id": "thinking", "value": True},
                    {"id": "effort", "value": "medium"},
                    {"id": "context", "value": "1m"},
                ]
            ),
            descriptor_path=descriptor,
            plugin_root=plugin,
        )
        if native["reason"] != "INCOMPLETE_LIFECYCLE" or native["blocked"] is not False:
            _fail(f"effort plus thinking did not bind reasoning: {native}")
        receipt = json.loads(descriptor.with_name(f"{'6' * 32}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        if receipt["handshake"]["reasoning"] != "medium":
            _fail("benchmark reasoning was not bound to effort")
        stored = descriptor.with_name(f"{'6' * 32}-CONTROL.receipt.json").read_text(encoding="utf-8")
        if "thinking" in stored or "1m" in stored:
            _fail("thinking mode or context was stored as reasoning")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="5" * 32)
        ambiguous = capture.ingest_hook(
            _session(model_params=[{"id": "effort", "value": "medium"}, {"id": "reasoning", "value": "high"}]),
            descriptor_path=Path(prepared["descriptor_path"]),
            plugin_root=Path(prepared["plugin_dir"]),
        )
        if ambiguous["reason"] != "PROFILE_AMBIGUOUS":
            _fail(f"ambiguous reasoning ids returned {ambiguous['reason']}")
        context_only = capture.ingest_hook(
            _session(model_params=[{"id": "context", "value": "medium"}]),
            descriptor_path=Path(prepared["descriptor_path"]),
            plugin_root=Path(prepared["plugin_dir"]),
        )
        if context_only["reason"] != "PROFILE_AMBIGUOUS":
            _fail(f"context-only after ambiguity returned {context_only['reason']}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="4" * 32)
        context_only = capture.ingest_hook(
            _session(model_params=[{"id": "context", "value": "medium"}]),
            descriptor_path=Path(prepared["descriptor_path"]),
            plugin_root=Path(prepared["plugin_dir"]),
        )
        if context_only["reason"] != "MISSING_PROFILE_EVIDENCE":
            _fail(f"context-only params returned {context_only['reason']}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="3" * 32)
        thinking_only = capture.ingest_hook(
            _session(model_params=[{"id": "thinking", "value": True}]),
            descriptor_path=Path(prepared["descriptor_path"]),
            plugin_root=Path(prepared["plugin_dir"]),
        )
        if thinking_only["reason"] != "MISSING_PROFILE_EVIDENCE":
            _fail(f"thinking-only params returned {thinking_only['reason']}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="f" * 32)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        capture.ingest_hook(_session(), descriptor_path=descriptor, plugin_root=plugin)
        mismatched = capture.ingest_hook(_shell(True), descriptor_path=descriptor, plugin_root=plugin)
        if mismatched["reason"] != "SANDBOX_MISMATCH" or mismatched["hook_response"] != {"permission": "deny"}:
            _fail(f"sandbox mismatch returned {mismatched}")
        if "echo-secret-command" in _stored_text(root):
            _fail("sandbox mismatch retained the shell command")
        if (root / "state" / f"{'f' * 32}-CONTROL.hmac-key").exists():
            _fail("sandbox mismatch left the HMAC key")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="9" * 32)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        capture.ingest_hook(_session(), descriptor_path=descriptor, plugin_root=plugin)
        aborted = capture.ingest_hook(
            {
                "hook_event_name": "sessionEnd",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "reason": "aborted",
            },
            descriptor_path=descriptor,
            plugin_root=plugin,
        )
        if aborted["reason"] != "INCOMPLETE_LIFECYCLE":
            _fail(f"aborted sessionEnd returned {aborted['reason']}")
        if (root / "state" / f"{'9' * 32}-CONTROL.hmac-key").exists():
            _fail("aborted lane left the HMAC key")
        bare_stop = capture.ingest_hook(
            {
                "hook_event_name": "stop",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "status": "completed",
            },
            descriptor_path=descriptor,
            plugin_root=plugin,
        )
        if bare_stop["reason"] != "INCOMPLETE_LIFECYCLE":
            _fail("aborted lane accepted a later stop")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="8" * 32)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        capture.ingest_hook(_session(), descriptor_path=descriptor, plugin_root=plugin)
        missing_sandbox = capture.ingest_hook(
            {
                "hook_event_name": "stop",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "status": "completed",
            },
            descriptor_path=descriptor,
            plugin_root=plugin,
        )
        if missing_sandbox["reason"] != "MISSING_SANDBOX":
            _fail(f"stop without sandbox returned {missing_sandbox['reason']}")


def test_blocking_hooks_deny_without_handshake() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        tool = capture.ingest_hook(
            {
                "hook_event_name": "preToolUse",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "tool_use_id": "tool-1",
                "tool_name": "Read",
                "tool_input": {"path": PATH_SECRET},
            },
            descriptor_path=descriptor,
            plugin_root=plugin,
        )
        if tool["blocked"] is not True or tool["hook_response"] != {"permission": "deny"}:
            _fail(f"preToolUse before handshake was {tool}")
        prompt = capture.ingest_hook(
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "generation_id": "gen-1",
                "prompt": SECRET,
            },
            descriptor_path=descriptor,
            plugin_root=plugin,
        )
        if prompt["hook_response"] != {"continue": False} or SECRET in _stored_text(root):
            _fail(f"beforeSubmitPrompt before handshake was {prompt}")


def test_recorder_does_not_echo_payload() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        completed = _run_recorder(root, prepared, json.dumps(_session()))
        if completed.returncode != 0:
            _fail(f"recorder failed: {completed.stderr}")
        if completed.stdout.strip() != "{}":
            _fail(f"recorder stdout was {completed.stdout}")
        if SECRET in completed.stdout or EMAIL_SECRET in completed.stdout:
            _fail("recorder echoed hook content")
        receipt = json.loads((root / "state" / f"{RUN_ID}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        if receipt["handshake"]["conversation_id"] != "conv-1":
            _fail("recorder did not bind the native conversation")
        allowed = _run_recorder(
            root,
            prepared,
            json.dumps(
                {
                    "hook_event_name": "preToolUse",
                    "conversation_id": "conv-1",
                    "cursor_version": CURSOR_VERSION,
                    "tool_use_id": "tool-1",
                    "tool_name": "Read",
                    "tool_input": {"path": PATH_SECRET},
                }
            ),
        )
        if allowed.returncode != 0 or json.loads(allowed.stdout) != {"permission": "allow"}:
            _fail(f"recorder denied a tool after handshake: {allowed.returncode} {allowed.stdout}")
        if PATH_SECRET in allowed.stdout:
            _fail("recorder echoed tool input")


def test_recorder_fails_closed_without_instrumentation() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        missing = _run_recorder(
            root,
            prepared,
            json.dumps({"hook_event_name": "preToolUse", "tool_input": {"path": PATH_SECRET}}),
            env={**os.environ},
        )
        if missing.returncode == 0 or json.loads(missing.stdout) != {"permission": "deny"}:
            _fail(f"missing instrumentation returned {missing.returncode} {missing.stdout}")
        if PATH_SECRET in missing.stdout:
            _fail("failed recorder echoed tool input")
        invalid = _run_recorder(root, prepared, "{")
        if invalid.returncode == 0 or "permission" not in invalid.stdout:
            _fail(f"invalid JSON returned {invalid.returncode} {invalid.stdout}")
        swapped = root / "swapped_adapter.py"
        swapped.write_bytes(TOOL.read_bytes() + b"\n")
        swapped_result = _run_recorder(
            root,
            prepared,
            json.dumps(_session()),
            env={
                **os.environ,
                "ES_BENCHMARK_LANE_DESCRIPTOR": prepared["env"]["ES_BENCHMARK_LANE_DESCRIPTOR"],
                "ES_BENCHMARK_TELEMETRY_MODULE": str(swapped),
                "ES_BENCHMARK_PLUGIN_ROOT": prepared["env"]["ES_BENCHMARK_PLUGIN_ROOT"],
            },
        )
        if swapped_result.returncode == 0:
            _fail("swapped adapter module was accepted")
        receipt_file = root / "state" / f"{RUN_ID}-CONTROL.receipt.json"
        if receipt_file.exists() and json.loads(receipt_file.read_text(encoding="utf-8"))["handshake"]:
            _fail("swapped adapter wrote a handshake")
        early = _run_recorder(
            root,
            prepared,
            json.dumps(
                {
                    "hook_event_name": "beforeSubmitPrompt",
                    "conversation_id": "conv-1",
                    "cursor_version": CURSOR_VERSION,
                    "generation_id": "gen-1",
                    "prompt": SECRET,
                }
            ),
        )
        if early.returncode == 0 or json.loads(early.stdout) != {"continue": False}:
            _fail(f"beforeSubmitPrompt without handshake returned {early.returncode} {early.stdout}")
        if SECRET in early.stdout:
            _fail("recorder echoed the prompt")


def _clock(stamps: list[str]):
    original = capture._utc_now
    pending = iter(stamps)

    def clock() -> str:
        return next(pending)

    capture._utc_now = clock
    return original


def _restore_clock(original) -> None:
    capture._utc_now = original


def _read(tool_id: str, hook: str, marker: str) -> dict:
    return {
        "hook_event_name": hook,
        "conversation_id": "conv-1",
        "cursor_version": CURSOR_VERSION,
        "generation_id": "gen-1",
        "tool_use_id": tool_id,
        "tool_name": "Read",
        "tool_input": {"secret": TOOL_SECRET, "marker": marker},
    }


def _stop(**extra: object) -> dict:
    payload = {
        "hook_event_name": "stop",
        "conversation_id": "conv-1",
        "cursor_version": CURSOR_VERSION,
        "status": "completed",
    }
    payload.update(extra)
    return payload


def _ingest(descriptor: Path, plugin: Path, payload: dict) -> dict:
    return capture.ingest_hook(payload, descriptor_path=descriptor, plugin_root=plugin)


def test_counts_come_from_native_events() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        events = [
            _session(),
            _shell(),
            _read("tool-1", "preToolUse", "same"),
            _read("tool-1", "postToolUseFailure", "same"),
            _read("tool-2", "preToolUse", "same"),
            _read("tool-2", "postToolUse", "same"),
            _read("tool-3", "preToolUse", "other"),
            _read("tool-3", "postToolUse", "other"),
            _read("tool-4", "preToolUse", "other"),
            _read("tool-4", "postToolUse", "other"),
            _read("tool-4", "postToolUse", "other"),
            {
                "hook_event_name": "preCompact",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "generation_id": "compact-1",
            },
            {
                "hook_event_name": "preCompact",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "generation_id": "compact-1",
            },
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "generation_id": "gen-1",
                "prompt": SECRET,
            },
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "generation_id": "gen-2",
                "prompt": SECRET,
            },
            _stop(),
        ]
        for payload in events:
            result = _ingest(descriptor, plugin, payload)
            if result["status"] != "READY" and payload["hook_event_name"] == "stop":
                _fail(f"qualified lifecycle blocked as {result['reason']}")
        receipt = json.loads((root / "state" / f"{RUN_ID}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        expected = {
            "tool_turns": 4,
            "retries": 1,
            "rereads": 2,
            "compactions": 2,
            "pr_rework": 0,
            "ci_rework": 0,
            "review_rework": 0,
            "human_interventions": 1,
        }
        if receipt.get("counts") != expected:
            _fail(f"counts were {receipt.get('counts')}")
        compactions = [item for item in receipt["events"] if item["hook_event_name"] == "preCompact"]
        if len(compactions) != 2 or {item["generation_id"] for item in compactions} != {"compact-1"}:
            _fail(f"same-generation compactions were {compactions}")
        if (root / "state" / f"{RUN_ID}-CONTROL.fingerprints.json").exists():
            _fail("finalized lane kept tool fingerprints")
        if receipt.get("usage") is not None:
            _fail("missing usage was stored")
        stored = _stored_text(root)
        for needle in (SECRET, TOOL_SECRET, PATH_SECRET, "echo-secret-command"):
            if needle in stored:
                _fail(f"receipt retained {needle}")


def test_truncated_tool_lifecycle_blocks() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        for payload in (_session(), _read("t1", "preToolUse", "once"), _shell(), _stop()):
            result = _ingest(descriptor, plugin, payload)
        if result["reason"] != "MISSING_TELEMETRY" or result["status"] != "BLOCK":
            _fail(f"truncated tool lifecycle was {result}")
        receipt = json.loads((root / "state" / f"{RUN_ID}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        if "counts" in receipt:
            _fail(f"truncated lifecycle stored counts {receipt['counts']}")
        finalized = capture.finalize_lane(descriptor, root=root)
        if finalized["reason"] != "MISSING_TELEMETRY" or "record" in finalized:
            _fail(f"truncated lifecycle finalized as {finalized}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        _ingest(descriptor, plugin, _session())
        orphan = _ingest(descriptor, plugin, _read("t1", "postToolUse", "once"))
        if orphan["reason"] != "MISSING_TELEMETRY":
            _fail(f"post without pre returned {orphan['reason']}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        _ingest(descriptor, plugin, _session())
        _ingest(descriptor, plugin, _read("t1", "preToolUse", "once"))
        _ingest(descriptor, plugin, _read("t1", "postToolUse", "once"))
        mixed = _ingest(descriptor, plugin, _read("t1", "postToolUseFailure", "once"))
        if mixed["reason"] != "DUPLICATE_EVENT":
            _fail(f"mixed tool outcome returned {mixed['reason']}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        for payload in (
            _session(),
            _shell(),
            _read("t1", "preToolUse", "once"),
            _read("t1", "postToolUseFailure", "once"),
            _stop(),
        ):
            result = _ingest(descriptor, plugin, payload)
        if result["status"] != "READY":
            _fail(f"single failure returned {result['reason']}")
        receipt = json.loads((root / "state" / f"{RUN_ID}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        counts = receipt.get("counts")
        if counts is None or counts["tool_turns"] != 1 or counts["retries"] != 0 or counts["rereads"] != 0:
            _fail(f"single failure counts were {counts}")


def test_missing_evidence_does_not_become_zero() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        _ingest(descriptor, plugin, _session())
        _ingest(descriptor, plugin, _shell())
        missing = _ingest(
            descriptor,
            plugin,
            {
                "hook_event_name": "postToolUse",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "tool_use_id": "tool-9",
                "tool_name": "Read",
            },
        )
        if missing["reason"] != "MISSING_TELEMETRY":
            _fail(f"tool without fingerprint returned {missing['reason']}")
        receipt = json.loads((root / "state" / f"{RUN_ID}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        if "counts" in receipt or receipt.get("counts") == 0:
            _fail("missing tool evidence became a count")
        early = capture.finalize_lane(descriptor, root=root)
        if early["reason"] != "MISSING_TELEMETRY" or early["execute_worker"] is not False or "record" in early:
            _fail(f"finalize of blocked lane was {early}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        _ingest(descriptor, plugin, _session())
        compact = _ingest(
            descriptor,
            plugin,
            {
                "hook_event_name": "preCompact",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
            },
        )
        if compact["reason"] != "MISSING_TELEMETRY":
            _fail(f"compaction without generation returned {compact['reason']}")
        rounded = _ingest(descriptor, plugin, _stop(duration_ms=1500))
        if rounded["reason"] != "MISSING_TELEMETRY":
            _fail("blocked lane accepted a later duration")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        _ingest(descriptor, plugin, _session())
        _ingest(descriptor, plugin, _shell())
        duration = _ingest(descriptor, plugin, _stop(duration_ms=1500))
        if duration["reason"] != "DURATION_AMBIGUOUS":
            _fail(f"non-integer duration returned {duration['reason']}")
        estimated = capture.finalize_lane(descriptor, root=root)
        if estimated["reason"] != "DURATION_AMBIGUOUS" or "record" in estimated:
            _fail(f"ambiguous duration finalized as {estimated}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        _ingest(descriptor, plugin, _session())
        _ingest(descriptor, plugin, _shell())
        usage = _ingest(descriptor, plugin, _stop(usage={"estimated_cost": 1}))
        if usage["reason"] != "USAGE_ESTIMATED":
            _fail(f"estimated usage returned {usage['reason']}")
        open_lane = capture.finalize_lane(descriptor, root=root)
        if open_lane["status"] != "BLOCK" or "record" in open_lane:
            _fail(f"estimated usage finalized as {open_lane}")


def test_finalize_writes_one_canonical_record() -> None:
    original = _clock(["2026-09-26T11:00:00Z", "2026-09-26T11:00:02Z"])
    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepared = _prepare(root)
            descriptor = Path(prepared["descriptor_path"])
            plugin = Path(prepared["plugin_dir"])
            plan = capture.benchmark_execution.persistent_lane_plan(prepared)
            if plan["execute_worker"] is not False or plan["argv"][:2] != ["--sandbox", "enabled"]:
                _fail(f"launch plan was {plan['argv']}")
            if plan["requested_sandbox"] == plan["expected_effective_sandbox"]:
                _fail("requested sandbox matched the effective sandbox")
            for payload in (_session(), _shell(), _stop(usage={"input_tokens": 12})):
                result = _ingest(descriptor, plugin, payload)
            if result["status"] != "READY":
                _fail(f"exposed usage lifecycle was {result}")
            incomplete = root / "partial"
            incomplete.mkdir()
            blocked = capture.finalize_lane(descriptor, root=incomplete)
            if blocked["reason"] != "EXACT_HEAD_UNVERIFIED" or "record" in blocked:
                _fail(f"unverified head finalized as {blocked}")
            common = subprocess.run(
                ["git", "-C", str(ROOT), "rev-parse", "--git-common-dir"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if common.returncode:
                _fail("control checkout source was unavailable")
            source = Path(common.stdout.strip())
            if not source.is_absolute():
                source = (ROOT / source).resolve()
            checkout = root / "control"
            cloned = subprocess.run(
                ["git", "clone", "--shared", "--no-checkout", str(source), str(checkout)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if cloned.returncode:
                _fail(f"control clone failed: {cloned.stderr.strip()}")
            detached = subprocess.run(
                ["git", "-C", str(checkout), "checkout", "--detach", capture.benchmark_fixture.CONTROL_HEAD],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if detached.returncode:
                _fail(f"control checkout failed: {detached.stderr.strip()}")
            finalized = capture.finalize_lane(descriptor, root=checkout)
            if finalized["status"] != "READY" or finalized["execute_worker"] is not False:
                _fail(f"finalize was {finalized}")
            record = finalized["record"]
            again = capture.finalize_lane(descriptor, root=checkout)
            if again.get("record") != record:
                _fail("second finalize changed the canonical record")
            if record["usage"]["input_tokens"] != 12 or record["usage"]["cost"] is not None:
                _fail(f"usage was {record['usage']}")
            if record["duration_seconds"] != 2 or record["terminal"] != "PASS":
                _fail(f"record identity was {record['duration_seconds']} {record['terminal']}")
            if record["validation"]["exact_head"] != capture.benchmark_fixture.CONTROL_HEAD:
                _fail("record exact head was not the control head")
            derived = capture.benchmark_execution.derive_observed_fields(
                record,
                lane="CONTROL",
                system_head=capture.benchmark_fixture.CONTROL_HEAD,
                profile=PROFILE,
                run_id=RUN_ID,
                case_id="BENCH-BUG-001",
            )
            if derived["WALL_SECONDS"] != 2 or derived["MODEL_COST"] != "UNKNOWN" or derived["EXACT_HEAD_EVIDENCE"] != "PASS":
                _fail(f"derived fields were {derived}")
            if derived["RETRIES"] != 0 or derived["HUMAN_INTERVENTIONS"] != 0 or derived["REVIEW_REWORK"] != 0:
                _fail(f"derived counts were {derived}")
            encoded = json.dumps(record)
            for needle in (SECRET, TOOL_SECRET, PATH_SECRET, EMAIL_SECRET, "echo-secret-command", "conv-1", "plugin-dir"):
                if needle in encoded:
                    _fail(f"canonical record retained {needle}")
            retained = checkout / ".git" / "engineering-system" / "telemetry" / f"{RUN_ID}.json"
            if not retained.is_file():
                _fail("canonical record was not stored in the git directory")
    finally:
        _restore_clock(original)


def main() -> None:
    tests = (
        test_plugin_is_hook_only,
        test_prepare_stays_outside_task_tree,
        test_handshake_and_gate,
        test_fail_closed_evidence,
        test_native_receipt_drops_content_and_duplicates,
        test_repository_identity_is_field_aware,
        test_stop_and_session_end_status_are_separate,
        test_duplicate_prepare_leaves_existing_lane_unchanged,
        test_profile_and_sandbox_parity,
        test_blocking_hooks_deny_without_handshake,
        test_recorder_does_not_echo_payload,
        test_recorder_fails_closed_without_instrumentation,
        test_counts_come_from_native_events,
        test_truncated_tool_lifecycle_blocks,
        test_missing_evidence_does_not_become_zero,
        test_finalize_writes_one_canonical_record,
    )
    for test in tests:
        print(f"RUN {test.__name__}", flush=True)
        test()
    print("PASS persistent benchmark telemetry")


if __name__ == "__main__":
    main()
