#!/usr/bin/env python3
"""Native benchmark hook receipts and canonical telemetry finalization."""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import inspect
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


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        _fail(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _clone_shared(destination: Path) -> None:
    common = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--git-common-dir"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if common.returncode:
        _fail("checkout source was unavailable")
    source = Path(common.stdout.strip())
    if not source.is_absolute():
        source = (ROOT / source).resolve()
    cloned = subprocess.run(
        ["git", "clone", "--shared", "--no-checkout", str(source), str(destination)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if cloned.returncode:
        _fail(f"clone failed: {cloned.stderr.strip()}")


def _frozen_checkout(destination: Path, *, head: str | None = None, origin: str | None = None) -> None:
    identity = capture.benchmark_execution.FROZEN_CASE_IDENTITIES["BENCH-BUG-001"]
    _clone_shared(destination)
    _git(destination, "remote", "set-url", "origin", origin or "https://github.com/datarelay-labs/engineering-system.git")
    _git(destination, "checkout", "--detach", head or identity["source_commit"])


def _prepare(root: Path, *, lane: str = "CONTROL", run_id: str = RUN_ID) -> dict:
    _frozen_checkout(root / "task")
    return capture.prepare_lane(
        state_dir=root / "state",
        task_root=root / "task",
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
        if _git(task, "status", "--porcelain"):
            _fail("historical task tree changed")
        identity = capture.benchmark_execution.FROZEN_CASE_IDENTITIES["BENCH-BUG-001"]
        if _git(task, "rev-parse", "HEAD") != identity["source_commit"]:
            _fail("historical task source changed")
        descriptor = json.loads(Path(prepared["descriptor_path"]).read_text(encoding="utf-8"))
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
            "model": PROFILE["model"],
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
        if stopped["status"] != "OPEN" or stopped["reason"] != "LOOP_CHECKPOINT" or stopped["blocked"] is not False:
            _fail(f"completed lifecycle was {stopped}")
        admitted = capture.gate_launch(descriptor)
        if admitted != {"admission": "BLOCK", "reason": "INCOMPLETE_LIFECYCLE", "execute_worker": False}:
            _fail(f"launch gate was {admitted}")
        if not (root / "state" / f"{RUN_ID}-CONTROL.hmac-key").exists():
            _fail("loop checkpoint deleted the HMAC key")
        open_receipt = json.loads((root / "state" / f"{RUN_ID}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        if "counts" in open_receipt or open_receipt["lifecycle"] != "OPEN" or open_receipt.get("loop") != "completed":
            _fail(f"stop sealed the lane: {open_receipt.get('lifecycle')} {open_receipt.get('loop')}")
        if "efficiency-telemetry" in _stored_text(root):
            _fail("checkpoint wrote a canonical telemetry record")


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
        tool_token = hashlib.sha256(b"tool-1").hexdigest()
        if fingerprints["fingerprints"].get(tool_token) != digest or digest == plain or "tool-1" in json.dumps(fingerprints):
            _fail("fingerprint was not a run-local HMAC of a canonical tool id")
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
        if stopped["status"] != "OPEN" or stopped["reason"] != "LOOP_CHECKPOINT":
            _fail(f"stop status completed was {stopped}")
        if not (root / "state" / f"{'c' * 32}-CONTROL.hmac-key").exists():
            _fail("stop checkpoint deleted the HMAC key")
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
        if closed["blocked"] is not False or closed["reason"] != "INCOMPLETE_LIFECYCLE":
            _fail(f"window_close sealed an open lane: {closed}")
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
        if user_close["reason"] != "INCOMPLETE_LIFECYCLE" or user_close["blocked"] is not False:
            _fail(f"user_close sealed an open lane: {user_close}")
        final = json.loads((root / "state" / f"{'c' * 32}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        if final["lifecycle"] != "OPEN" or final["block"] is not None or final.get("loop") != "completed":
            _fail("session close sealed or corrupted the open receipt")

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
        if ended["blocked"] is not False or ended["reason"] != "INCOMPLETE_LIFECYCLE":
            _fail(f"sessionEnd reason completed was {ended}")
        ended_receipt = json.loads((root / "state" / f"{'e' * 32}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        if ended_receipt["lifecycle"] == "COMPLETE" or "counts" in ended_receipt:
            _fail("sessionEnd sealed the lane")


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
        descriptor = Path(prepared["descriptor_path"])
        missing = capture.ingest_hook(
            _session(model_params=[]),
            descriptor_path=descriptor,
            plugin_root=Path(prepared["plugin_dir"]),
        )
        if missing["reason"] != "INCOMPLETE_LIFECYCLE" or missing["blocked"] is not False:
            _fail(f"sessionStart without model_params returned {missing}")
        bound = json.loads(descriptor.with_name(f"{'7' * 32}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        if bound["handshake"].get("model") != PROFILE["model"] or bound["handshake"].get("reasoning") != "medium":
            _fail("Cursor model id did not bind model and reasoning")

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
        descriptor = Path(prepared["descriptor_path"])
        context_only = capture.ingest_hook(
            _session(model_params=[{"id": "context", "value": "medium"}]),
            descriptor_path=descriptor,
            plugin_root=Path(prepared["plugin_dir"]),
        )
        if context_only["reason"] != "INCOMPLETE_LIFECYCLE" or context_only["blocked"] is not False:
            _fail(f"context-only sessionStart returned {context_only}")
        stored = descriptor.with_name(f"{'4' * 32}-CONTROL.receipt.json").read_text(encoding="utf-8")
        if "1m" in stored or "context" in stored:
            _fail("context-only sessionStart stored a context param")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="3" * 32)
        descriptor = Path(prepared["descriptor_path"])
        thinking_only = capture.ingest_hook(
            _session(model_params=[{"id": "thinking", "value": True}]),
            descriptor_path=descriptor,
            plugin_root=Path(prepared["plugin_dir"]),
        )
        if thinking_only["reason"] != "INCOMPLETE_LIFECYCLE" or thinking_only["blocked"] is not False:
            _fail(f"thinking-only sessionStart returned {thinking_only}")
        if "thinking" in descriptor.with_name(f"{'3' * 32}-CONTROL.receipt.json").read_text(encoding="utf-8"):
            _fail("thinking mode was stored as reasoning")

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


def _read(tool_id: str, hook: str, marker: str, tool_name: str = "Read") -> dict:
    return {
        "hook_event_name": hook,
        "conversation_id": "conv-1",
        "cursor_version": CURSOR_VERSION,
        "generation_id": "gen-1",
        "tool_use_id": tool_id,
        "tool_name": tool_name,
        "tool_input": {"secret": TOOL_SECRET, "marker": marker},
    }


def _derived(root: Path, run_id: str = RUN_ID, lane: str = "CONTROL") -> dict:
    receipt = json.loads((root / "state" / f"{run_id}-{lane}.receipt.json").read_text(encoding="utf-8"))
    fingerprints = json.loads((root / "state" / f"{run_id}-{lane}.fingerprints.json").read_text(encoding="utf-8"))
    return capture._derive_counts(receipt, fingerprints["fingerprints"])


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


def _control_checkout(destination: Path) -> None:
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
    cloned = subprocess.run(
        ["git", "clone", "--shared", "--no-checkout", str(source), str(destination)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if cloned.returncode:
        _fail(f"control clone failed: {cloned.stderr.strip()}")
    detached = subprocess.run(
        ["git", "-C", str(destination), "checkout", "--detach", capture.benchmark_fixture.CONTROL_HEAD],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if detached.returncode:
        _fail(f"control checkout failed: {detached.stderr.strip()}")


def _prompt(generation: str) -> dict:
    return {
        "hook_event_name": "beforeSubmitPrompt",
        "conversation_id": "conv-1",
        "cursor_version": CURSOR_VERSION,
        "generation_id": generation,
        "prompt": SECRET,
    }


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
            _read("tool-write", "preToolUse", "write", "Write"),
            _read("tool-write", "postToolUse", "write", "Write"),
            _stop(),
        ]
        for payload in events:
            result = _ingest(descriptor, plugin, payload)
            if payload["hook_event_name"] == "stop" and result["reason"] != "LOOP_CHECKPOINT":
                _fail(f"qualified lifecycle blocked as {result['reason']}")
        open_receipt = json.loads((root / "state" / f"{RUN_ID}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        if "counts" in open_receipt or not (root / "state" / f"{RUN_ID}-CONTROL.fingerprints.json").exists():
            _fail("stop froze counts or deleted fingerprints")
        expected = {
            "tool_turns": 5,
            "retries": 1,
            "rereads": 2,
            "compactions": 2,
            "pr_rework": 0,
            "ci_rework": 0,
            "review_rework": 0,
            "human_interventions": 1,
        }
        if _derived(root) != expected:
            _fail(f"counts were {_derived(root)}")
        checkout = root / "control"
        _control_checkout(checkout)
        finalized = capture.finalize_lane(descriptor, root=checkout)
        if finalized["reason"] != "TRUST_BOUNDARY_UNAVAILABLE" or "record" in finalized:
            _fail(f"untrusted count finalization was {finalized}")
        receipt = json.loads((root / "state" / f"{RUN_ID}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        if "counts" in receipt or receipt.get("lifecycle") == "COMPLETE":
            _fail("missing host authority sealed the lane")
        if (checkout / ".git" / "engineering-system" / "telemetry" / f"{RUN_ID}.json").exists():
            _fail("missing host authority wrote canonical telemetry")
        compactions = [item for item in receipt["events"] if item["hook_event_name"] == "preCompact"]
        if len(compactions) != 2 or {item["generation_id"] for item in compactions} != {"compact-1"}:
            _fail(f"same-generation compactions were {compactions}")
        if not (root / "state" / f"{RUN_ID}-CONTROL.fingerprints.json").exists():
            _fail("untrusted finalization deleted tool fingerprints")
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
        if result["reason"] != "LOOP_CHECKPOINT":
            _fail(f"single failure returned {result['reason']}")
        counts = _derived(root)
        if counts["tool_turns"] != 1 or counts["retries"] != 0 or counts["rereads"] != 0:
            _fail(f"single failure counts were {counts}")
        checkout = root / "control"
        _control_checkout(checkout)
        finalized = capture.finalize_lane(descriptor, root=checkout)
        if "record" in finalized or finalized["reason"] == "TELEMETRY_RECORDED":
            _fail(f"single failure recorded telemetry as {finalized}")


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

    original = _clock(["2026-09-26T11:00:00Z", "2026-09-26T11:00:00Z"])
    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepared = _prepare(root)
            descriptor = Path(prepared["descriptor_path"])
            plugin = Path(prepared["plugin_dir"])
            _ingest(descriptor, plugin, _session())
            _ingest(descriptor, plugin, _shell())
            noted = _ingest(descriptor, plugin, _stop(duration_ms=2000))
            if noted["reason"] != "LOOP_CHECKPOINT":
                _fail(f"whole-second duration checkpoint returned {noted['reason']}")
            checkout = root / "control"
            _control_checkout(checkout)
            mismatched = capture.finalize_lane(descriptor, root=checkout)
            if mismatched["reason"] != "DURATION_AMBIGUOUS" or "record" in mismatched:
                _fail(f"mismatched duration finalized as {mismatched}")
            open_receipt = json.loads((root / "state" / f"{RUN_ID}-CONTROL.receipt.json").read_text(encoding="utf-8"))
            if open_receipt["lifecycle"] == "COMPLETE" or "counts" in open_receipt:
                _fail("mismatched duration sealed the lane")
    finally:
        _restore_clock(original)

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


def test_follow_up_prompt_is_one_human_intervention() -> None:
    original = _clock(["2026-09-26T12:00:00Z", "2026-09-26T12:00:04Z"])
    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepared = _prepare(root)
            descriptor = Path(prepared["descriptor_path"])
            plugin = Path(prepared["plugin_dir"])
            for payload in (
                _session(),
                _prompt("gen-1"),
                _shell(),
                _read("tool-write", "preToolUse", "write", "Write"),
                _read("tool-write", "postToolUse", "write", "Write"),
                _stop(),
            ):
                result = _ingest(descriptor, plugin, payload)
            if result["reason"] != "LOOP_CHECKPOINT" or result["blocked"] is not False:
                _fail(f"first stop was {result}")
            receipt = json.loads((root / "state" / f"{RUN_ID}-CONTROL.receipt.json").read_text(encoding="utf-8"))
            if "counts" in receipt or receipt["lifecycle"] != "OPEN" or not (root / "state" / f"{RUN_ID}-CONTROL.hmac-key").exists():
                _fail("first stop sealed the lane or dropped the correlation key")
            follow = _ingest(descriptor, plugin, _prompt("gen-2"))
            if follow["blocked"] is not False or follow["hook_response"] != {"continue": True}:
                _fail(f"follow-up prompt was {follow}")
            tool = _ingest(descriptor, plugin, _read("tool-follow", "preToolUse", "follow-up"))
            if tool["reason"] == "FINGERPRINT_KEY_MISSING" or tool["blocked"] is not False:
                _fail(f"follow-up tool was {tool}")
            posted = _ingest(descriptor, plugin, _read("tool-follow", "postToolUse", "follow-up"))
            if posted["blocked"] is not False:
                _fail(f"follow-up tool result was {posted}")
            second = _ingest(descriptor, plugin, _stop())
            if second["reason"] != "LOOP_CHECKPOINT":
                _fail(f"second stop was {second}")
            counts = _derived(root)
            if counts["human_interventions"] != 1 or counts["tool_turns"] != 2:
                _fail(f"follow-up counts were {counts}")
            checkout = root / "control"
            _control_checkout(checkout)
            finalized = capture.finalize_lane(descriptor, root=checkout)
            if finalized["reason"] != "TRUST_BOUNDARY_UNAVAILABLE" or finalized["execute_worker"] is not False or "record" in finalized:
                _fail(f"follow-up finalize was {finalized}")
            if not (root / "state" / f"{RUN_ID}-CONTROL.hmac-key").exists() or not (root / "state" / f"{RUN_ID}-CONTROL.fingerprints.json").exists():
                _fail("missing host authority deleted correlation state")
            if (checkout / ".git" / "engineering-system" / "telemetry" / f"{RUN_ID}.json").exists():
                _fail("missing host authority wrote canonical telemetry")
            open_receipt = json.loads((root / "state" / f"{RUN_ID}-CONTROL.receipt.json").read_text(encoding="utf-8"))
            if open_receipt["lifecycle"] == "COMPLETE" or "counts" in open_receipt:
                _fail("missing host authority sealed the follow-up lane")
            sealed = dict(open_receipt)
            sealed["lifecycle"] = "COMPLETE"
            sealed["counts"] = counts
            (root / "state" / f"{RUN_ID}-CONTROL.receipt.json").write_text(
                json.dumps(sealed, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            sealed = json.loads((root / "state" / f"{RUN_ID}-CONTROL.receipt.json").read_text(encoding="utf-8"))
            if sealed["counts"]["human_interventions"] != 1:
                _fail("follow-up count was lost before the post-seal check")
            later = _ingest(descriptor, plugin, _read("tool-after", "preToolUse", "after-seal"))
            if later["reason"] != "LANE_SEALED" or later["blocked"] is not True or later["hook_response"] != {"permission": "deny"}:
                _fail(f"post-seal tool was {later}")
            unchanged = json.loads((root / "state" / f"{RUN_ID}-CONTROL.receipt.json").read_text(encoding="utf-8"))
            if unchanged != sealed or unchanged.get("block") is not None:
                _fail("post-seal hook mutated the sealed receipt")
            closed = _ingest(
                descriptor,
                plugin,
                {
                    "hook_event_name": "sessionEnd",
                    "conversation_id": "conv-1",
                    "cursor_version": CURSOR_VERSION,
                    "reason": "window_close",
                },
            )
            if closed["status"] != "READY" or closed["blocked"] is not False:
                _fail(f"window_close after seal was {closed}")
            if json.loads((root / "state" / f"{RUN_ID}-CONTROL.receipt.json").read_text(encoding="utf-8")) != sealed:
                _fail("window_close after seal changed the receipt")
    finally:
        _restore_clock(original)


def test_finalize_writes_one_canonical_record() -> None:
    original = _clock(["2026-09-26T11:00:00Z", "2026-09-26T11:00:02Z", "2026-09-26T11:00:02Z"])
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
            for payload in (
                _session(),
                _shell(),
                _read("tool-write", "preToolUse", "write", "Write"),
                _read("tool-write", "postToolUse", "write", "Write"),
                _stop(usage={"input_tokens": 12}),
            ):
                result = _ingest(descriptor, plugin, payload)
            if result["reason"] != "LOOP_CHECKPOINT":
                _fail(f"exposed usage lifecycle was {result}")
            incomplete = root / "partial"
            incomplete.mkdir()
            blocked = capture.finalize_lane(descriptor, root=incomplete)
            if blocked["reason"] != "EXACT_HEAD_UNVERIFIED" or "record" in blocked:
                _fail(f"unverified head finalized as {blocked}")
            checkout = root / "control"
            _control_checkout(checkout)
            finalized = capture.finalize_lane(descriptor, root=checkout)
            if finalized["reason"] != "TRUST_BOUNDARY_UNAVAILABLE" or finalized["execute_worker"] is not False or "record" in finalized:
                _fail(f"finalize was {finalized}")
            again = capture.finalize_lane(descriptor, root=checkout)
            if again != finalized:
                _fail("second finalize changed the fail-closed result")
            retained = checkout / ".git" / "engineering-system" / "telemetry" / f"{RUN_ID}.json"
            if retained.exists():
                _fail("missing host authority stored a canonical record")
            receipt = json.loads((root / "state" / f"{RUN_ID}-CONTROL.receipt.json").read_text(encoding="utf-8"))
            if receipt.get("lifecycle") == "COMPLETE" or "counts" in receipt:
                _fail("missing host authority sealed a legitimate lane")
            if receipt.get("usage", {}).get("input_tokens") != 12:
                _fail(f"exposed usage was {receipt.get('usage')}")
    finally:
        _restore_clock(original)


def _expect_checkout(task: Path, snapshot: Path, code: str) -> None:
    try:
        capture.prepare_lane(
            state_dir=snapshot.parent / "state",
            task_root=task,
            snapshot_dir=snapshot,
            run_id=RUN_ID,
            case_id="BENCH-BUG-001",
            lane="CONTROL",
            profile=PROFILE,
            cursor_version=CURSOR_VERSION,
            expected_effective_sandbox=EXPECTED_EFFECTIVE_SANDBOX,
        )
    except capture.CaptureError as exc:
        if exc.code != code:
            _fail(f"checkout returned {exc.code}")
    else:
        _fail(f"checkout {code} prepared a lane")
    if snapshot.exists() or (snapshot.parent / "state").exists():
        _fail(f"{code} left lane files")


def _sticky_clock(start: str, finish: str):
    original = capture._utc_now
    state = {"n": 0}

    def clock() -> str:
        state["n"] += 1
        return start if state["n"] == 1 else finish

    capture._utc_now = clock
    return original


def _fixtures():
    path = ROOT / "tools" / "skills_contract_fixtures.py"
    spec = importlib.util.spec_from_file_location("skills_contract_fixtures", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dirty_frozen_checkout_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        modified = root / "modified"
        _frozen_checkout(modified)
        agents = modified / "AGENTS.md"
        agents.write_text(agents.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        _expect_checkout(modified, root / "modified-plugin", "TASK_CHECKOUT_DIRTY")

        untracked = root / "untracked"
        _frozen_checkout(untracked)
        (untracked / "notes.tmp").write_text("x\n", encoding="utf-8")
        _expect_checkout(untracked, root / "untracked-plugin", "TASK_CHECKOUT_DIRTY")

        staged = root / "staged"
        _frozen_checkout(staged)
        (staged / "staged.tmp").write_text("x\n", encoding="utf-8")
        _git(staged, "add", "--", "staged.tmp")
        _expect_checkout(staged, root / "staged-plugin", "TASK_CHECKOUT_DIRTY")

        submodule = root / "submodule"
        _frozen_checkout(submodule)
        gitlink = _git(submodule, "rev-parse", "HEAD")
        _git(submodule, "update-index", "--add", "--cacheinfo", f"160000,{gitlink},vendor/drift")
        _expect_checkout(submodule, root / "submodule-plugin", "TASK_CHECKOUT_DIRTY")


def test_task_checkout_binds_frozen_identity() -> None:
    identity = capture.benchmark_execution.FROZEN_CASE_IDENTITIES["BENCH-BUG-001"]
    if identity["source_commit"] == capture.benchmark_fixture.CONTROL_HEAD:
        _fail("wrong-head fixture matches the frozen source")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        plain = root / "plain"
        plain.mkdir()
        _expect_checkout(plain, root / "plain-plugin", "TASK_CHECKOUT_UNVERIFIED")
        wrong_head = root / "wrong-head"
        _frozen_checkout(wrong_head, head=capture.benchmark_fixture.CONTROL_HEAD)
        _expect_checkout(wrong_head, root / "head-plugin", "TASK_HEAD_MISMATCH")
        wrong_repo = root / "wrong-repo"
        _frozen_checkout(wrong_repo, origin="https://github.com/datarelay-labs/datarelay-link.git")
        _expect_checkout(wrong_repo, root / "repo-plugin", "TASK_REPOSITORY_MISMATCH")


def test_toolset_and_receipt_are_not_worker_authoritative() -> None:
    recorder = (capture.PLUGIN_ROOT / "hooks" / "record.py").read_text(encoding="utf-8")
    if "ES_BENCHMARK_TRUST_DIR" in recorder or "finalize_lane" in recorder or "CoordinatorReceiptWitness" in recorder:
        _fail("hook recorder can reach receipt authority")
    if capture.host_receipt_authority_available():
        _fail("this host reported a receipt anchor that is not provisioned")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        foreign = dict(PROFILE)
        foreign["toolset"] = "read-only"
        (root / "task").mkdir()
        try:
            capture.prepare_lane(
                state_dir=root / "state",
                task_root=root / "task",
                snapshot_dir=root / "plugin",
                run_id=RUN_ID,
                case_id="BENCH-BUG-001",
                lane="CONTROL",
                profile=foreign,
                cursor_version=CURSOR_VERSION,
                expected_effective_sandbox=EXPECTED_EFFECTIVE_SANDBOX,
            )
        except capture.CaptureError as exc:
            if exc.code != "TOOLSET_UNKNOWN":
                _fail(f"descriptor toolset returned {exc.code}")
        else:
            _fail("descriptor toolset was admitted")
        if (root / "plugin").exists():
            _fail("rejected toolset staged a plugin")
        prepared = _prepare(root)
        if "trust_dir" in prepared or "receipt-key" in json.dumps(prepared):
            _fail("prepare returned receipt-authority material")
        if set(prepared["env"]) != {
            "ES_BENCHMARK_LANE_DESCRIPTOR",
            "ES_BENCHMARK_TELEMETRY_MODULE",
            "ES_BENCHMARK_PLUGIN_ROOT",
        }:
            _fail(f"worker env was {sorted(prepared['env'])}")
        if list(root.rglob("*.receipt-key")):
            _fail("prepare minted a receipt key")
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        _ingest(descriptor, plugin, _session())
        _ingest(descriptor, plugin, _shell())
        stopped = _ingest(descriptor, plugin, _stop())
        if stopped["reason"] != "LOOP_CHECKPOINT":
            _fail(f"shell-only stop was {stopped['reason']}")
        checkout = root / "control"
        _control_checkout(checkout)
        missing = capture.finalize_lane(descriptor, root=checkout)
        if missing["reason"] != "TRUST_BOUNDARY_UNAVAILABLE" or "record" in missing:
            _fail(f"shell-only subset was {missing}")
        shell_receipt = json.loads((root / "state" / f"{RUN_ID}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        if shell_receipt.get("observed_tools") != ["Shell"] or shell_receipt.get("lifecycle") == "COMPLETE":
            _fail(f"shell-only receipt was {shell_receipt.get('observed_tools')} {shell_receipt.get('lifecycle')}")
        extra = _ingest(descriptor, plugin, _read("web", "preToolUse", "web", "WebSearch"))
        if extra["reason"] != "TOOLSET_MISMATCH":
            _fail(f"extra tool was {extra['reason']}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        _ingest(descriptor, plugin, _session())
        unknown = _ingest(descriptor, plugin, _read("mystery", "preToolUse", "mystery", "NoSuchTool"))
        if unknown["reason"] != "TOOLSET_UNKNOWN":
            _fail(f"unknown tool was {unknown['reason']}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        _ingest(descriptor, plugin, _session())
        mcp = _ingest(
            descriptor,
            plugin,
            {
                "hook_event_name": "beforeMCPExecution",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "tool_name": "mcp",
                "mcp_server_name": "example",
            },
        )
        if mcp["reason"] != "TOOLSET_MISMATCH":
            _fail(f"mcp tool was {mcp['reason']}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        for payload in (
            _session(),
            _shell(),
            _read("tool-write", "preToolUse", "write", "Write"),
            _read("tool-write", "postToolUse", "write", "Write"),
            _stop(),
        ):
            _ingest(descriptor, plugin, payload)
        receipt_path = root / "state" / f"{RUN_ID}-CONTROL.receipt.json"
        forged = json.loads(receipt_path.read_text(encoding="utf-8"))
        forged["lifecycle"] = "COMPLETE"
        forged["counts"] = {
            "tool_turns": 0,
            "retries": 0,
            "rereads": 0,
            "compactions": 0,
            "pr_rework": 0,
            "ci_rework": 0,
            "review_rework": 0,
            "human_interventions": 0,
        }
        forged["duration_seconds"] = 1
        forged["finished_at"] = "2026-09-26T11:00:01Z"
        forged["effective_toolset"] = "write-shell-allowlist"
        receipt_path.write_text(json.dumps(forged) + "\n", encoding="utf-8")
        hidden = root / ".hidden-trust"
        hidden.mkdir()
        key = hidden / "lane.receipt-key"
        key.write_bytes(b"same-uid-secret")
        key.chmod(0o600)
        os.environ["ES_BENCHMARK_TRUST_DIR"] = str(hidden)
        try:
            checkout = root / "control"
            _control_checkout(checkout)
            forged_result = capture.finalize_lane(descriptor, root=checkout)
            if forged_result["reason"] != "TRUST_BOUNDARY_UNAVAILABLE" or "record" in forged_result:
                _fail(f"forged receipt finalized as {forged_result}")
            if hasattr(capture, "CoordinatorReceiptWitness"):
                _fail("in-process witness class remains a production authority")
            parameters = inspect.signature(capture.finalize_lane).parameters
            if set(parameters) != {"descriptor_path", "root"} or any(
                item.kind in {item.VAR_KEYWORD, item.VAR_POSITIONAL} for item in parameters.values()
            ):
                _fail(f"finalize accepts caller authority via {list(parameters)}")
            try:
                capture.finalize_lane(descriptor, root=checkout, witness={"receipt": forged})  # type: ignore[call-arg]
            except TypeError:
                pass
            else:
                _fail("dict witness was accepted")
            if (checkout / ".git" / "engineering-system" / "telemetry" / f"{RUN_ID}.json").exists():
                _fail("forged receipt wrote canonical telemetry")
        finally:
            os.environ.pop("ES_BENCHMARK_TRUST_DIR", None)


def test_signed_subset_records_allowlist() -> None:
    """Shell without Write/Edit stays the allowlist, and a worker cannot self-authorize telemetry."""
    source = TOOL.read_text(encoding="utf-8")
    if "class CoordinatorReceiptWitness" in source or "trust_root.mkdir" in source or ".receipt-key" in source:
        _fail("production telemetry still mints a worker-visible receipt authority")
    if "os.environ" in source or "_TEST_HOST_RECEIPT" in source or "test_mode" in source:
        _fail("production telemetry selects trust material from the environment or a test seam")
    if hasattr(capture, "_bind_host_finalizer") or hasattr(capture, "_TEST_HOST_RECEIPT_ANCHOR"):
        _fail("production module still exposes a trust-selection seam")
    original = _sticky_clock("2026-09-26T11:00:00Z", "2026-09-26T11:00:02Z")
    previous = {
        "anchor": capture.HOST_RECEIPT_ANCHOR,
        "directory": capture.HOST_RECEIPT_DIR,
        "anchor_ok": capture._host_anchor_ok,
        "directory_ok": capture._host_dir_ok,
    }
    planted = sys.modules.get("skills_contract")
    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepared = _prepare(root)
            descriptor = Path(prepared["descriptor_path"])
            plugin = Path(prepared["plugin_dir"])
            for payload in (_session(), _shell(), _stop()):
                result = _ingest(descriptor, plugin, payload)
            if result["reason"] != "LOOP_CHECKPOINT":
                _fail(f"shell-only stop was {result['reason']}")
            checkout = root / "control"
            _control_checkout(checkout)
            binding = capture.host_receipt_binding(descriptor, root=checkout)
            if binding["effective_toolset"] != "write-shell-allowlist" or binding["lifecycle"] != "COMPLETE":
                _fail(f"host binding was {binding}")
            fixtures = _fixtures()
            private, public = fixtures.generate_keypair(root / "keys")
            signed = fixtures.sign_payload(private, binding)
            skills_spec = importlib.util.spec_from_file_location(
                "skills_contract_assertion_check",
                ROOT / "tools" / "skills-contract.py",
            )
            assert skills_spec and skills_spec.loader
            skills = importlib.util.module_from_spec(skills_spec)
            sys.modules["skills_contract_assertion_check"] = skills
            try:
                skills_spec.loader.exec_module(skills)
                if not skills.ed25519_verify(public, fixtures.canonical_payload_bytes(signed), signed["signature"]):
                    _fail("skills-contract rejected a fixture signature for the bounded receipt")
            finally:
                sys.modules.pop("skills_contract_assertion_check", None)
            host_dir = root / "host-receipts"
            host_dir.mkdir()
            assertion = host_dir / f"{RUN_ID}-CONTROL.host-receipt.json"
            assertion.write_text(json.dumps(signed) + "\n", encoding="utf-8")
            os.environ["ENGINEERING_SKILLS_TRUST_ANCHOR_PUBKEY"] = str(public)
            os.environ["ES_BENCHMARK_TRUST_DIR"] = str(host_dir)
            capture.HOST_RECEIPT_ANCHOR = public
            capture.HOST_RECEIPT_DIR = host_dir
            capture._TEST_HOST_RECEIPT_ANCHOR = public
            capture._TEST_HOST_RECEIPT_DIR = host_dir
            capture._host_anchor_ok = lambda _path: True
            capture._host_dir_ok = lambda _path: True

            class _FakeSkills:
                HOST_OPENSSL_PATH = "/usr/bin/openssl"
                __file__ = str(public)

                @staticmethod
                def verify_signed_json(_path, _anchor):
                    return dict(signed)

                @staticmethod
                def ed25519_verify(_anchor, _message, _signature):
                    return True

            sys.modules["skills_contract"] = _FakeSkills()
            finalized = capture.finalize_lane(descriptor, root=checkout)
            if finalized["reason"] != "TRUST_BOUNDARY_UNAVAILABLE" or "record" in finalized or finalized["execute_worker"] is not False:
                _fail(f"monkeypatched trust finalized as {finalized}")
            retained = checkout / ".git" / "engineering-system" / "telemetry" / f"{RUN_ID}.json"
            if retained.exists():
                _fail("monkeypatched trust wrote canonical telemetry")
            receipt = json.loads((root / "state" / f"{RUN_ID}-CONTROL.receipt.json").read_text(encoding="utf-8"))
            if receipt.get("lifecycle") == "COMPLETE" or "counts" in receipt:
                _fail("monkeypatched trust sealed the lane")
            if capture.host_receipt_authority_available():
                _fail("monkeypatched paths made the fixed anchor look present")
    finally:
        os.environ.pop("ENGINEERING_SKILLS_TRUST_ANCHOR_PUBKEY", None)
        os.environ.pop("ES_BENCHMARK_TRUST_DIR", None)
        capture.HOST_RECEIPT_ANCHOR = previous["anchor"]
        capture.HOST_RECEIPT_DIR = previous["directory"]
        capture._host_anchor_ok = previous["anchor_ok"]
        capture._host_dir_ok = previous["directory_ok"]
        if hasattr(capture, "_TEST_HOST_RECEIPT_ANCHOR"):
            delattr(capture, "_TEST_HOST_RECEIPT_ANCHOR")
        if hasattr(capture, "_TEST_HOST_RECEIPT_DIR"):
            delattr(capture, "_TEST_HOST_RECEIPT_DIR")
        if planted is None:
            sys.modules.pop("skills_contract", None)
        else:
            sys.modules["skills_contract"] = planted
        _restore_clock(original)


def _native_prompt(conversation: str = "conv-1", **extra: object) -> dict:
    payload = {
        "hook_event_name": "beforeSubmitPrompt",
        "conversation_id": conversation,
        "cursor_version": CURSOR_VERSION,
        "generation_id": "gen-1",
        "model": PROFILE["model"],
        "model_id": PROFILE["model"],
        "model_params": [{"id": "effort", "value": "medium"}],
        "prompt": SECRET,
    }
    payload.update(extra)
    return payload


def _activation(**extra: object) -> dict:
    payload = {
        "hook_event_name": "sessionStart",
        "conversation_id": "conv-1",
        "session_id": "conv-1",
        "cursor_version": CURSOR_VERSION,
    }
    payload.update(extra)
    return payload


def test_profile_binds_from_before_submit_prompt() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="b" * 32)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        started = _ingest(descriptor, plugin, _activation())
        if started["reason"] != "INCOMPLETE_LIFECYCLE" or started["blocked"] is not False:
            _fail(f"unresolved sessionStart returned {started}")
        receipt = json.loads(descriptor.with_name(f"{'b' * 32}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        if "model" in receipt["handshake"] or "reasoning" in receipt["handshake"]:
            _fail("unresolved sessionStart bound a profile")
        bound = _ingest(descriptor, plugin, _native_prompt())
        if bound["blocked"] is not False:
            _fail(f"profile-bearing beforeSubmitPrompt returned {bound}")
        receipt = json.loads(descriptor.with_name(f"{'b' * 32}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        if receipt["handshake"].get("model") != PROFILE["model"] or receipt["handshake"].get("reasoning") != "medium":
            _fail(f"beforeSubmitPrompt did not bind the native profile: {receipt['handshake']}")
        stored = descriptor.with_name(f"{'b' * 32}-CONTROL.receipt.json").read_text(encoding="utf-8")
        if SECRET in stored or "model_id" in stored or "prompt" in stored:
            _fail("profile binding retained raw prompt or model_id")
        shell = _ingest(descriptor, plugin, _shell())
        if shell["blocked"] is not False:
            _fail(f"shell after profile binding returned {shell}")
        stopped = _ingest(descriptor, plugin, _stop())
        if stopped["reason"] != "LOOP_CHECKPOINT":
            _fail(f"stop after deferred profile binding returned {stopped}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="c" * 32)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        _ingest(descriptor, plugin, _activation())
        tool = _ingest(
            descriptor,
            plugin,
            {
                "hook_event_name": "preToolUse",
                "conversation_id": "conv-1",
                "cursor_version": CURSOR_VERSION,
                "tool_use_id": "tool-1",
                "tool_name": "Read",
                "tool_input": {"path": PATH_SECRET},
            },
        )
        if tool["reason"] != "MISSING_PROFILE_EVIDENCE" or tool["blocked"] is not True:
            _fail(f"tool before profile returned {tool}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="e" * 32)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        _ingest(descriptor, plugin, _activation())
        missing = _ingest(descriptor, plugin, _native_prompt(model_params=None))
        if missing["reason"] != "MISSING_PROFILE_EVIDENCE":
            _fail(f"beforeSubmitPrompt without model_params returned {missing['reason']}")
        mismatched = None

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="1" * 32)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        _ingest(descriptor, plugin, _activation())
        mismatched = _ingest(descriptor, plugin, _native_prompt(model_params=[{"id": "effort", "value": "high"}]))
        if mismatched["reason"] != "PROFILE_MISMATCH":
            _fail(f"prompt reasoning mismatch returned {mismatched['reason']}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="2" * 32)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        _ingest(descriptor, plugin, _activation())
        ambiguous = _ingest(
            descriptor,
            plugin,
            _native_prompt(model_params=[{"id": "effort", "value": "medium"}, {"id": "reasoning", "value": "high"}]),
        )
        if ambiguous["reason"] != "PROFILE_AMBIGUOUS":
            _fail(f"prompt ambiguity returned {ambiguous['reason']}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="0" * 32)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        _ingest(descriptor, plugin, _activation())
        foreign = _ingest(descriptor, plugin, _native_prompt(conversation="conv-2"))
        if foreign["reason"] != "SESSION_MISMATCH":
            _fail(f"foreign prompt returned {foreign['reason']}")
        version = _ingest(descriptor, plugin, _native_prompt(cursor_version="2026.09.26-aaaaaaa"))
        if version["reason"] != "SESSION_MISMATCH":
            _fail(f"prompt after foreign session returned {version['reason']}")
        receipt = json.loads(descriptor.with_name(f"{'0' * 32}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        if "reasoning" in receipt.get("handshake", {}):
            _fail("foreign profile event bound reasoning")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="f1" + "a" * 30)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        _ingest(descriptor, plugin, _activation())
        version = _ingest(descriptor, plugin, _native_prompt(cursor_version="2026.09.26-aaaaaaa"))
        if version["reason"] != "VERSION_MISMATCH":
            _fail(f"foreign prompt version returned {version['reason']}")


def _native_tool(event: str, tool_use_id: object, **extra: object) -> dict:
    payload = {
        "hook_event_name": event,
        "conversation_id": "123e4567-e89b-12d3-a456-426614174000",
        "session_id": "123e4567-e89b-12d3-a456-426614174000",
        "cursor_version": CURSOR_VERSION,
        "generation_id": "123e4567-e89b-12d3-a456-426614174001",
        "model": "",
        "tool_name": "Read",
        "tool_use_id": tool_use_id,
        "cwd": PATH_SECRET,
        "tool_input": {"file_path": PATH_SECRET},
    }
    payload.update(extra)
    return payload


def test_opaque_tool_use_id_is_canonical_correlation() -> None:
    native = "tool_" + ("n" * 77) + "\n"
    if len(native) != 83 or "\n" not in native or "_" not in native:
        _fail("fixture is not the observed opaque tool id shape")
    canonical = hashlib.sha256(native.encode("utf-8")).hexdigest()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        run_id = "a1" + "b" * 30
        prepared = _prepare(root, run_id=run_id)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        started = _ingest(
            descriptor,
            plugin,
            _session(
                conversation="123e4567-e89b-12d3-a456-426614174000",
                generation_id="123e4567-e89b-12d3-a456-426614174002",
                model_params=None,
            ),
        )
        if started["blocked"] is not False:
            _fail(f"canonical sessionStart returned {started}")
        read = _ingest(descriptor, plugin, _native_tool("preToolUse", native, model="gpt-5.6-sol"))
        if read["blocked"] is not False:
            _fail(f"opaque preToolUse returned {read}")
        posted = _ingest(
            descriptor,
            plugin,
            _native_tool("postToolUse", native, model="gpt-5.6-sol", tool_output=OUTPUT_SECRET),
        )
        if posted["blocked"] is not False:
            _fail(f"opaque postToolUse returned {posted}")
        receipt_path = descriptor.with_name(f"{run_id}-CONTROL.receipt.json")
        fingerprint_path = descriptor.with_name(f"{run_id}-CONTROL.fingerprints.json")
        stored = receipt_path.read_text(encoding="utf-8") + fingerprint_path.read_text(encoding="utf-8")
        if native in stored or PATH_SECRET in stored or OUTPUT_SECRET in stored:
            _fail("receipt retained the raw tool id or tool content")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        tool_events = [item for item in receipt["events"] if item.get("tool_use_id")]
        if [item["tool_use_id"] for item in tool_events] != [canonical, canonical]:
            _fail(f"canonical tool correlation was {[item['tool_use_id'] for item in tool_events]}")
        if receipt["events"][1].get("generation_id") != "123e4567-e89b-12d3-a456-426614174001":
            _fail("strict generation id was not retained")
        mismatched = _ingest(descriptor, plugin, _native_tool("postToolUse", native + "x"))
        if mismatched["reason"] != "MISSING_TELEMETRY":
            _fail(f"mismatched opaque tool id returned {mismatched['reason']}")

    negatives = (
        ("a2" + "c" * 30, "", "MISSING_TELEMETRY"),
        ("a3" + "c" * 30, None, "MISSING_TELEMETRY"),
        ("a4" + "c" * 30, "a" * (capture.TOOL_USE_ID_MAX_BYTES + 1), "IDENTITY_INVALID"),
        ("a5" + "c" * 30, "/tmp/secret-tool", "IDENTITY_INVALID"),
        ("a6" + "c" * 30, "sk-live-abcdefghij", "PROHIBITED_CONTENT"),
        ("a7" + "d" * 30, 12, "IDENTITY_INVALID"),
    )
    for run_id, tool_use_id, expected in negatives:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepared = _prepare(root, run_id=run_id)
            descriptor = Path(prepared["descriptor_path"])
            plugin = Path(prepared["plugin_dir"])
            _ingest(descriptor, plugin, _session())
            rejected = _ingest(
                descriptor,
                plugin,
                _native_tool("preToolUse", tool_use_id, conversation_id="conv-1", session_id="conv-1"),
            )
            if rejected["reason"] != expected:
                _fail(f"tool id {tool_use_id!r} returned {rejected['reason']}")


def test_cursor_model_id_binds_profile_and_base_projection() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="b1" + "e" * 30)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        started = _ingest(descriptor, plugin, _session(model_params=None, model="gpt-5.6-sol-medium"))
        if started["blocked"] is not False:
            _fail(f"canonical Cursor model id returned {started}")
        receipt = json.loads(descriptor.with_name(f"{'b1' + 'e' * 30}-CONTROL.receipt.json").read_text(encoding="utf-8"))
        if receipt["handshake"].get("model") != "gpt-5.6-sol-medium" or receipt["handshake"].get("reasoning") != "medium":
            _fail(f"canonical model id handshake was {receipt['handshake']}")
        agreed = _ingest(
            descriptor,
            plugin,
            _read("tool-base", "preToolUse", "base", "Read") | {"model": "gpt-5.6-sol"},
        )
        if agreed["blocked"] is not False:
            _fail(f"base model projection returned {agreed}")

    for run_id, model in (
        ("b2" + "e" * 30, "gpt-5.6-sol-high"),
        ("b3" + "e" * 30, "gpt-5.6-sol-low"),
        ("b4" + "e" * 30, "gpt-4.1"),
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepared = _prepare(root, run_id=run_id)
            descriptor = Path(prepared["descriptor_path"])
            plugin = Path(prepared["plugin_dir"])
            _ingest(descriptor, plugin, _session(model_params=None))
            rejected = _ingest(
                descriptor,
                plugin,
                _read("tool-other", "preToolUse", "other", "Read") | {"model": model},
            )
            if rejected["reason"] != "PROFILE_MISMATCH":
                _fail(f"model {model} returned {rejected['reason']}")

    for run_id, model in (
        ("b8" + "e" * 30, "gpt-5.6-sol-high"),
        ("b9" + "e" * 30, "gpt-5.6-sol"),
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepared = _prepare(root, run_id=run_id)
            rejected = _ingest(
                Path(prepared["descriptor_path"]),
                Path(prepared["plugin_dir"]),
                _session(model_params=None, model=model),
            )
            if rejected["reason"] != "PROFILE_MISMATCH":
                _fail(f"sessionStart model {model} returned {rejected['reason']}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="ba" + "e" * 30)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        _ingest(descriptor, plugin, _session(model_params=None))
        malformed = _ingest(
            descriptor,
            plugin,
            _read("tool-malformed", "preToolUse", "malformed", "Read")
            | {"model": "gpt-5.6-sol", "model_params": {"id": "effort", "value": "medium"}},
        )
        if malformed["reason"] != "MISSING_PROFILE_EVIDENCE":
            _fail(f"non-list model_params returned {malformed['reason']}")


def test_replaced_skills_contract_cannot_record_telemetry() -> None:
    skills = ROOT / "tools" / "skills-contract.py"
    execution = ROOT / "tools" / "benchmark_execution.py"
    skills_original = skills.read_bytes()
    execution_original = execution.read_bytes()
    source = (ROOT / "tools" / "persistent_benchmark_telemetry.py").read_text(encoding="utf-8")
    if "skills-contract.py" in source or "exec_module" in source or "host_signed_payload" in source or "pkeyutl" in source:
        _fail("finalizer still trusts same-UID verifier code")
    if "/usr/lib/engineering-system/benchmark-receipt-verify" not in source:
        _fail("finalizer does not require the root receipt helper")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        sentinel = root / "verifier-executed"
        worker_helper = root / "benchmark-receipt-verify"
        worker_helper.write_text("#!/bin/sh\n" + f"echo executed > {sentinel}\nexit 0\n", encoding="utf-8")
        worker_helper.chmod(0o755)
        run_id = "c1" + "a" * 30
        prepared = _prepare(root, run_id=run_id)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        for payload in (_session(), _shell(), _stop()):
            result = _ingest(descriptor, plugin, payload)
        if result["reason"] != "LOOP_CHECKPOINT":
            _fail(f"replacement setup returned {result['reason']}")
        checkout = root / "control"
        _control_checkout(checkout)

        def forged_payload(*_args, **_kwargs):
            sentinel.write_text("executed", encoding="utf-8")
            return {"kind": "benchmark-host-receipt", "lifecycle": "COMPLETE", "signature": "forged"}

        capture.benchmark_execution.host_signed_payload = forged_payload
        skills.write_text(
            "HOST_OPENSSL_PATH = '/usr/bin/openssl'\n"
            "def verify_signed_json(path, anchor):\n"
            f"    open({str(sentinel)!r}, 'w').write('executed')\n"
            "    return {'kind': 'benchmark-host-receipt', 'lifecycle': 'COMPLETE', 'signature': 'forged'}\n",
            encoding="utf-8",
        )
        execution.write_text(
            "def host_signed_payload(assertion, anchor):\n"
            f"    open({str(sentinel)!r}, 'w').write('executed')\n"
            "    return {'kind': 'benchmark-host-receipt', 'lifecycle': 'COMPLETE', 'signature': 'forged'}\n",
            encoding="utf-8",
        )
        try:
            finalized = capture.finalize_lane(descriptor, root=checkout)
        finally:
            skills.write_bytes(skills_original)
            execution.write_bytes(execution_original)
            if hasattr(capture.benchmark_execution, "host_signed_payload"):
                delattr(capture.benchmark_execution, "host_signed_payload")
        if finalized["reason"] != "TRUST_BOUNDARY_UNAVAILABLE" or "record" in finalized or finalized["execute_worker"] is not False:
            _fail(f"replaced same-UID verifier recorded telemetry as {finalized}")
        if sentinel.exists():
            _fail("replaced same-UID verifier was executed during finalization")
        recorded = checkout / ".git" / "engineering-system" / "telemetry" / f"{run_id}.json"
        if recorded.exists():
            _fail("replaced same-UID verifier wrote a telemetry record")


def test_optional_empty_generation_is_not_a_prompt_id() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="b5" + "f" * 30)
        started = _ingest(
            Path(prepared["descriptor_path"]),
            Path(prepared["plugin_dir"]),
            _activation(generation_id=""),
        )
        if started["reason"] == "IDENTITY_INVALID" or started["blocked"] is not False:
            _fail(f"empty generation on sessionStart returned {started}")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="b6" + "f" * 30)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        _ingest(descriptor, plugin, _session())
        _ingest(descriptor, plugin, _shell())
        stopped = _ingest(descriptor, plugin, _stop(generation_id=""))
        if stopped["reason"] != "LOOP_CHECKPOINT":
            _fail(f"empty generation on stop returned {stopped}")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepared = _prepare(root, run_id="b7" + "f" * 30)
        descriptor = Path(prepared["descriptor_path"])
        plugin = Path(prepared["plugin_dir"])
        _ingest(descriptor, plugin, _session())
        prompt = _ingest(descriptor, plugin, _native_prompt(generation_id=""))
        if prompt["reason"] != "MISSING_TELEMETRY":
            _fail(f"empty generation on beforeSubmitPrompt returned {prompt['reason']}")


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
        test_profile_binds_from_before_submit_prompt,
        test_opaque_tool_use_id_is_canonical_correlation,
        test_cursor_model_id_binds_profile_and_base_projection,
        test_optional_empty_generation_is_not_a_prompt_id,
        test_blocking_hooks_deny_without_handshake,
        test_recorder_does_not_echo_payload,
        test_recorder_fails_closed_without_instrumentation,
        test_counts_come_from_native_events,
        test_truncated_tool_lifecycle_blocks,
        test_missing_evidence_does_not_become_zero,
        test_follow_up_prompt_is_one_human_intervention,
        test_finalize_writes_one_canonical_record,
        test_dirty_frozen_checkout_is_rejected,
        test_task_checkout_binds_frozen_identity,
        test_toolset_and_receipt_are_not_worker_authoritative,
        test_signed_subset_records_allowlist,
        test_replaced_skills_contract_cannot_record_telemetry,
    )
    for test in tests:
        print(f"RUN {test.__name__}", flush=True)
        test()
    print("PASS persistent benchmark telemetry")


if __name__ == "__main__":
    main()
