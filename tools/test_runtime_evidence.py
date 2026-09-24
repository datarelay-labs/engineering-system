#!/usr/bin/env python3
"""Regressions for authorized read-only runtime evidence."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import runtime_evidence  # noqa: E402
import skills_contract  # noqa: E402
import skills_contract_fixtures as fixtures  # noqa: E402

from runtime_evidence import collect  # noqa: E402

INCIDENT = "INC-20260924-runtime"
CAPTURE = "CAP-20260924T000000Z-abcdef12"
REPO = "datarelay-labs/engineering-system"


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return completed.stdout.strip()


def init_repo(root: Path, command: str, *, logs: str | None = None, extra: dict[str, str] | None = None) -> str:
    root.mkdir(parents=True)
    (root / "health.py").write_text(
        "from pathlib import Path\nPath('runs').open('a').write('1')\nprint('ok')\n",
        encoding="utf-8",
    )
    engineering = root / ".engineering"
    engineering.mkdir()
    (engineering / "project.yaml").write_text(
        "operations:\n  health_command: " + json.dumps(command) + "\n",
        encoding="utf-8",
    )
    if extra:
        for name, content in extra.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    if logs is not None:
        (engineering / "runtime.yaml").write_text(logs, encoding="utf-8")
        schema_dir = root / "schemas"
        schema_dir.mkdir()
        (schema_dir / "runtime-contract.schema.json").write_text(
            (ROOT / "schemas" / "runtime-contract.schema.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    git(root, "init")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "test")
    git(root, "add", ".")
    git(root, "commit", "-m", "init")
    git(root, "remote", "add", "origin", f"https://github.com/{REPO}.git")
    return git(root, "rev-parse", "HEAD")


def effect(
    head: str,
    command: str,
    kind: str = "health",
    field: str = "operations.health_command",
    capture: str = CAPTURE,
) -> dict:
    return {
        "canonical_field_or_capability": field,
        "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
        "evidence_kind": kind,
        "incident_id": INCIDENT,
        "retention_subject": f"engineering-system/incidents/{INCIDENT}/{capture}.json",
        "subject_head": head,
        "target_repo": REPO,
    }


def signed(root: Path, base: Path, payload: dict) -> dict:
    priv, pub = fixtures.generate_keypair(base / "keys")
    _, _, digest = skills_contract.load_effective_state(ROOT)
    binding = base / "binding.json"
    dispatch = base / "dispatch.json"
    fixtures.write_binding_assertion(
        binding,
        private_key=priv,
        public_key=pub,
        profile="production_read",
        policy_digest=digest,
        authority_permission="admin",
        approved_classes=["production_read"],
    )
    fixtures.write_dispatch_assertion(
        dispatch,
        private_key=priv,
        public_key=pub,
        tool_id="production.read",
        classes=skills_contract.DEFAULT_TOOL_REGISTRY["production.read"],
        policy_digest=digest,
        request_payload=payload,
        dispatch_id="p1c-runtime-" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12],
        expires_at_unix=int(time.time()) + 3600,
    )
    request = dict(payload)
    request["verification"] = {"binding_assertion": str(binding), "dispatch_assertion": str(dispatch)}
    return request, pub


def trust(pub: Path):
    skills_contract._TEST_TRUST_ANCHOR_PATH = pub
    skills_contract._TEST_REPLAY_BOUNDARY_AVAILABLE = True
    skills_contract._TEST_REPLAY_STORE = set()


def clear_trust() -> None:
    skills_contract._TEST_TRUST_ANCHOR_PATH = None
    skills_contract._TEST_REPLAY_BOUNDARY_AVAILABLE = None
    skills_contract._TEST_REPLAY_STORE = None


def runs(root: Path) -> int:
    path = root / "runs"
    return len(path.read_text(encoding="utf-8")) if path.exists() else 0


def test_exact_head_health_executes_once() -> None:
    command = "python3 health.py"
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        head = init_repo(repo, command)
        request, pub = signed(repo, base, effect(head, command))
        trust(pub)
        try:
            report = collect(repo, request)
            assert report["RESULT"] == "CAPTURED"
            assert report["EXECUTED"] == "YES"
            assert report["MITIGATION_AUTHORITY"] == "NONE"
            assert "ok" not in "\n".join(report.values())
            assert runs(repo) == 0
            private = Path(git(repo, "rev-parse", "--absolute-git-dir")) / report["EVIDENCE_REF"]
            body = json.loads(private.read_text(encoding="utf-8"))
            assert body["raw_output"] == "ok\n"
            assert private.stat().st_mode & 0o777 == 0o600
            again = collect(repo, request)
            assert again["RESULT"] == "CAPTURED"
            assert again["EXECUTED"] == "NO"
            assert runs(repo) == 0
        finally:
            clear_trust()


def test_request_command_is_rejected() -> None:
    command = "python3 health.py"
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        head = init_repo(repo, command)
        request, pub = signed(repo, base, effect(head, command))
        request["command"] = "python3 -c 'print(1)'"
        trust(pub)
        try:
            report = collect(repo, request)
            assert report["RESULT"] == "AUTHORIZATION_DENIED"
            assert report["EXECUTED"] == "NO"
            assert runs(repo) == 0
        finally:
            clear_trust()


def test_stale_head_does_not_execute() -> None:
    command = "python3 health.py"
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        head = init_repo(repo, command)
        payload = effect("a" * 40, command)
        assert payload["subject_head"] != head
        report = collect(repo, payload)
        assert report["RESULT"] == "STALE_HEAD"
        assert report["EXECUTED"] == "NO"
        assert runs(repo) == 0


def test_missing_provenance_does_not_execute() -> None:
    command = "python3 health.py"
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        head = init_repo(repo, command)
        report = collect(repo, effect(head, command))
        assert report["RESULT"] == "BOUNDARY_UNAVAILABLE"
        assert report["EXECUTED"] == "NO"
        assert runs(repo) == 0


def test_command_hash_mismatch_does_not_execute() -> None:
    command = "python3 health.py"
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        head = init_repo(repo, command)
        payload = effect(head, command)
        payload["command_sha256"] = "b" * 64
        report = collect(repo, payload)
        assert report["RESULT"] == "AUTHORIZATION_DENIED"
        assert report["REASON"] == "COMMAND_HASH_MISMATCH"
        assert runs(repo) == 0


def test_unsupported_capability() -> None:
    command = "python3 health.py"
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        head = init_repo(repo, command)
        payload = effect(head, command, kind="logs", field="logs")
        report = collect(repo, payload)
        assert report["RESULT"] == "UNSUPPORTED"
        assert report["EXECUTED"] == "NO"


def test_timeout_and_unbounded_output_do_not_retry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        command = 'python3 -c "import time; time.sleep(30)"'
        head = init_repo(repo, command)
        request, pub = signed(repo, base, effect(head, command))
        trust(pub)
        previous = runtime_evidence.TIMEOUT_SECONDS
        runtime_evidence.TIMEOUT_SECONDS = 0.2
        try:
            report = collect(repo, request)
            assert report["RESULT"] == "TIMEOUT"
            assert report["EXECUTED"] == "YES"
        finally:
            runtime_evidence.TIMEOUT_SECONDS = previous
            clear_trust()
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        command = 'python3 -c "print(\'x\' * 50)"'
        head = init_repo(repo, command)
        request, pub = signed(repo, base, effect(head, command))
        trust(pub)
        previous = runtime_evidence.OUTPUT_LIMIT_BYTES
        runtime_evidence.OUTPUT_LIMIT_BYTES = 8
        try:
            report = collect(repo, request)
            assert report["RESULT"] == "OUTPUT_UNBOUNDED"
            assert "xxxx" not in "\n".join(report.values())
        finally:
            runtime_evidence.OUTPUT_LIMIT_BYTES = previous
            clear_trust()


def test_sensitive_output_is_not_published() -> None:
    command = "python3 -c \"print('ghp_abcdefghijklmnopqrstuvwxyz012345')\""
    secret = "ghp_abcdefghijklmnopqrstuvwxyz012345"
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        head = init_repo(repo, command)
        request, pub = signed(repo, base, effect(head, command))
        trust(pub)
        try:
            report = collect(repo, request)
            assert report["RESULT"] == "BLOCKED_SENSITIVE_OUTPUT"
            assert secret not in "\n".join(report.values())
            assert report["EVIDENCE_REF"] == ""
        finally:
            clear_trust()


def test_excluded_kinds_are_rejected() -> None:
    command = "python3 health.py"
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        head = init_repo(repo, command)
        for kind in ("start", "cleanup", "smoke", "e2e", "rollback", "deploy"):
            payload = effect(head, command, kind=kind, field=kind)
            report = collect(repo, payload)
            assert report["RESULT"] == "AUTHORIZATION_DENIED", kind
            assert report["EXECUTED"] == "NO"
        assert runs(repo) == 0


def test_capture_grants_no_mitigation_authority() -> None:
    text = (ROOT / "tools" / "runtime_evidence.py").read_text(encoding="utf-8")
    assert 'choices=("collect",)' in text
    assert "mitigate" not in text


def test_dirty_metadata_cannot_replace_committed_command() -> None:
    command = "python3 health.py"
    dirty = "python3 -c \"open('dirty-ran','w').write('1')\""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        head = init_repo(repo, command)
        project = repo / ".engineering" / "project.yaml"
        project.write_text(project.read_text(encoding="utf-8").replace(command, dirty), encoding="utf-8")
        request, pub = signed(repo, base, effect(head, command))
        trust(pub)
        try:
            report = collect(repo, request)
            assert report["RESULT"] == "CAPTURED"
            private = json.loads((Path(git(repo, "rev-parse", "--absolute-git-dir")) / report["EVIDENCE_REF"]).read_text(encoding="utf-8"))
            assert private["raw_output"] == "ok\n"
            assert runs(repo) == 0
            assert not (repo / "dirty-ran").exists()
            denied = effect(head, command)
            denied["command_sha256"] = hashlib.sha256(dirty.encode()).hexdigest()
            mismatch = collect(repo, denied)
            assert mismatch["RESULT"] == "AUTHORIZATION_DENIED"
            assert mismatch["REASON"] == "COMMAND_HASH_MISMATCH"
            assert mismatch["EXECUTED"] == "NO"
            assert runs(repo) == 0
        finally:
            clear_trust()


def test_output_bound_terminates_before_completion() -> None:
    command = (
        "python3 -c \"import sys,time,pathlib; sys.stdout.write('x'*64); sys.stdout.flush(); "
        "time.sleep(30); pathlib.Path('finished').write_text('yes')\""
    )
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        head = init_repo(repo, command)
        request, pub = signed(repo, base, effect(head, command))
        trust(pub)
        previous_limit = runtime_evidence.OUTPUT_LIMIT_BYTES
        runtime_evidence.OUTPUT_LIMIT_BYTES = 8
        started = time.monotonic()
        try:
            report = collect(repo, request)
            elapsed = time.monotonic() - started
            assert report["RESULT"] == "OUTPUT_UNBOUNDED"
            assert report["EXECUTED"] == "YES"
            assert elapsed < 2
            assert not (repo / "finished").exists()
            assert "xxxx" not in "\n".join(report.values())
        finally:
            runtime_evidence.OUTPUT_LIMIT_BYTES = previous_limit
            clear_trust()


def test_retention_symlink_and_bounds_fail_closed() -> None:
    import incident_evidence

    command = "python3 health.py"
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        outside = base / "outside"
        outside.mkdir()
        head = init_repo(repo, command)
        request, pub = signed(repo, base, effect(head, command))
        git_dir = Path(git(repo, "rev-parse", "--absolute-git-dir"))
        escaped = git_dir / "engineering-system"
        escaped.symlink_to(outside, target_is_directory=True)
        trust(pub)
        try:
            report = collect(repo, request)
            assert report["RESULT"] == "UNAVAILABLE"
            assert report["REASON"] == "RETENTION_PATH_ESCAPE"
            assert report["EXECUTED"] == "NO"
            assert report["EVIDENCE_REF"] == ""
            assert list(outside.iterdir()) == []
            assert runs(repo) == 0
        finally:
            clear_trust()
            escaped.unlink()
        retention = git_dir / "engineering-system" / "incidents" / INCIDENT
        retention.mkdir(parents=True)
        for index in range(incident_evidence.MAX_CAPTURES_PER_INCIDENT):
            (retention / f"extra-{index}.json").write_text("{}\n", encoding="utf-8")
        bounded_capture = "CAP-20260924T010203Z-bbbbbbbb"
        bounded_request, bounded_pub = signed(repo, base / "bounded", effect(head, command, capture=bounded_capture))
        trust(bounded_pub)
        try:
            bounded = collect(repo, bounded_request)
            assert bounded["RESULT"] == "UNAVAILABLE"
            assert bounded["REASON"] == "RETENTION_COUNT_BOUND"
            assert bounded["EXECUTED"] == "NO"
            assert runs(repo) == 0
        finally:
            clear_trust()


def test_dirty_executable_content_cannot_run() -> None:
    command = "python3 health.py"
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        marker = base / "dirty-ran"
        head = init_repo(repo, command)
        (repo / "helper.py").write_text("MARKER='committed'\n", encoding="utf-8")
        (repo / "health.py").write_text(
            "import helper\nprint(helper.MARKER)\n",
            encoding="utf-8",
        )
        git(repo, "add", "health.py", "helper.py")
        git(repo, "commit", "-m", "helper")
        head = git(repo, "rev-parse", "HEAD")
        (repo / "helper.py").write_text("MARKER='DIRTY_SCRIPT_RAN'\n", encoding="utf-8")
        (repo / "health.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('yes')\nprint('DIRTY_SCRIPT_RAN')\n",
            encoding="utf-8",
        )
        (repo / "only-dirty.txt").write_text("DIRTY_SCRIPT_RAN\n", encoding="utf-8")
        request, pub = signed(repo, base, effect(head, command))
        trust(pub)
        try:
            report = collect(repo, request)
            assert report["RESULT"] == "CAPTURED"
            assert report["EXECUTED"] == "YES"
            private = json.loads((Path(git(repo, "rev-parse", "--absolute-git-dir")) / report["EVIDENCE_REF"]).read_text(encoding="utf-8"))
            assert private["raw_output"] == "committed\n"
            assert "DIRTY_SCRIPT_RAN" not in private["raw_output"]
            assert not marker.exists()
        finally:
            clear_trust()


def test_bounded_stop_terminates_descendants() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        pidfile = base / "grandchild.pid"
        sentinel = base / "grandchild-alive"
        script = (
            "import os,sys,time,pathlib\n"
            f"pidfile=pathlib.Path({str(pidfile)!r})\n"
            f"sentinel=pathlib.Path({str(sentinel)!r})\n"
            "if os.fork()==0:\n"
            "    if os.fork()==0:\n"
            "        pidfile.write_text(str(os.getpid()))\n"
            "        time.sleep(30)\n"
            "        sentinel.write_text('yes')\n"
            "        os._exit(0)\n"
            "    os._exit(0)\n"
            "deadline=time.time()+5\n"
            "while not pidfile.exists():\n"
            "    if time.time()>deadline:\n"
            "        os._exit(1)\n"
            "    time.sleep(0.01)\n"
            "sys.stdout.write('x'*64)\n"
            "sys.stdout.flush()\n"
            "time.sleep(30)\n"
        )
        command = "python3 spawn.py"
        head = init_repo(repo, command, extra={"spawn.py": script})
        request, pub = signed(repo, base, effect(head, command))
        trust(pub)
        previous_limit = runtime_evidence.OUTPUT_LIMIT_BYTES
        runtime_evidence.OUTPUT_LIMIT_BYTES = 8
        started = time.monotonic()
        try:
            report = collect(repo, request)
            elapsed = time.monotonic() - started
            assert report["RESULT"] == "OUTPUT_UNBOUNDED", report
            assert report["EXECUTED"] == "YES"
            assert elapsed < 3
            assert pidfile.exists()
            pid = int(pidfile.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and Path(f"/proc/{pid}").exists():
                time.sleep(0.05)
            assert not Path(f"/proc/{pid}").exists()
            assert not sentinel.exists()
        finally:
            runtime_evidence.OUTPUT_LIMIT_BYTES = previous_limit
            clear_trust()


def _git_file_set(repo: Path) -> set[str]:
    git_dir = Path(git(repo, "rev-parse", "--absolute-git-dir"))
    return {str(path.relative_to(git_dir)) for path in git_dir.rglob("*") if path.is_file()}


def test_subject_tree_does_not_mutate_git_or_run_hooks() -> None:
    command = "python3 health.py"
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        marker = base / "hooked"
        head = init_repo(repo, command)
        hook = repo / ".git" / "hooks" / "post-checkout"
        hook.write_text(f"#!/bin/sh\necho hook >> {marker}\n", encoding="utf-8")
        hook.chmod(0o755)
        smudge = base / "smudge.sh"
        smudge.write_text(
            f"#!/bin/sh\necho smudge >> {marker}\nprintf '%s\\n' \"print('SMUDGED')\"\n",
            encoding="utf-8",
        )
        smudge.chmod(0o755)
        git(repo, "config", "core.hooksPath", str(hook.parent))
        git(repo, "config", "filter.evil.clean", "cat")
        git(repo, "config", "filter.evil.smudge", str(smudge))
        (repo / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
        git(repo, "add", ".gitattributes")
        git(repo, "commit", "-m", "attributes")
        head = git(repo, "rev-parse", "HEAD")
        before = _git_file_set(repo)
        request, pub = signed(repo, base, effect(head, command))
        trust(pub)
        try:
            report = collect(repo, request)
            assert report["RESULT"] == "CAPTURED", report
            private = json.loads(
                (Path(git(repo, "rev-parse", "--absolute-git-dir")) / report["EVIDENCE_REF"]).read_text(encoding="utf-8")
            )
            assert private["raw_output"] == "ok\n"
            assert "SMUDGED" not in private["raw_output"]
            assert not marker.exists()
            assert not (repo / ".git" / "worktrees").exists()
            added = _git_file_set(repo) - before
            assert added == {f"engineering-system/incidents/{INCIDENT}/{CAPTURE}.json"}
            assert not (before - _git_file_set(repo))
        finally:
            clear_trust()
    source = (ROOT / "tools" / "runtime_evidence.py").read_text(encoding="utf-8")
    assert "worktree add" not in source
    assert "worktree remove" not in source


def test_symlink_subject_tree_does_not_execute() -> None:
    command = "python3 health.py"
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        outside = base / "outside.py"
        outside.write_text("print('DIRTY_SCRIPT_RAN')\n", encoding="utf-8")
        head = init_repo(repo, command)
        (repo / "health.py").unlink()
        (repo / "health.py").symlink_to(outside)
        git(repo, "add", "health.py")
        git(repo, "commit", "-m", "symlink")
        head = git(repo, "rev-parse", "HEAD")
        request, pub = signed(repo, base, effect(head, command))
        trust(pub)
        try:
            report = collect(repo, request)
            assert report["RESULT"] == "EXECUTION_FAILED"
            assert report["REASON"] == "SUBJECT_TREE"
            assert report["EXECUTED"] == "NO"
            assert "DIRTY_SCRIPT_RAN" not in "\n".join(report.values())
        finally:
            clear_trust()


def main() -> int:
    test_exact_head_health_executes_once()
    test_request_command_is_rejected()
    test_stale_head_does_not_execute()
    test_missing_provenance_does_not_execute()
    test_command_hash_mismatch_does_not_execute()
    test_unsupported_capability()
    test_timeout_and_unbounded_output_do_not_retry()
    test_sensitive_output_is_not_published()
    test_excluded_kinds_are_rejected()
    test_capture_grants_no_mitigation_authority()
    test_dirty_metadata_cannot_replace_committed_command()
    test_output_bound_terminates_before_completion()
    test_retention_symlink_and_bounds_fail_closed()
    test_dirty_executable_content_cannot_run()
    test_bounded_stop_terminates_descendants()
    test_subject_tree_does_not_mutate_git_or_run_hooks()
    test_symlink_subject_tree_does_not_execute()
    print("RUNTIME_EVIDENCE_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
