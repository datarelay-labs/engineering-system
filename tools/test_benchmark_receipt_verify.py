#!/usr/bin/env python3
"""Deterministic checks for the root-owned benchmark receipt helper."""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import benchmark_receipt_verify as helper  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("skills_contract", TOOLS / "skills-contract.py")
assert _SPEC is not None and _SPEC.loader is not None
skills_contract = importlib.util.module_from_spec(_SPEC)
sys.modules["skills_contract"] = skills_contract
_SPEC.loader.exec_module(skills_contract)

HOST_PATHS = (
    Path(helper.HELPER_PATH),
    Path(helper.ANCHOR_PATH),
    Path(helper.PRIVATE_KEY_PATH),
    Path(helper.RECEIPT_DIR),
)
RUN_ID = "ab" * 16
OTHER_RUN_ID = "cd" * 16


def _fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(repository),
            "-c",
            "user.email=receipt@example.com",
            "-c",
            "user.name=receipt",
            *args,
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin",
            "LC_ALL": "C",
            "GIT_AUTHOR_NAME": "receipt",
            "GIT_AUTHOR_EMAIL": "receipt@example.com",
            "GIT_COMMITTER_NAME": "receipt",
            "GIT_COMMITTER_EMAIL": "receipt@example.com",
        },
    )
    if completed.returncode != 0:
        _fail(f"git {' '.join(args)} failed: {completed.stderr}")
    return completed.stdout.strip()


def _fixture_provenance(path: Path, *, expect_file: bool) -> bool:
    """Apply the mode and symlink rules while treating this fixture owner as root."""
    try:
        if path.is_symlink():
            return False
        if expect_file and not path.is_file():
            return False
        if not expect_file and not path.is_dir():
            return False
        if path.stat().st_mode & 0o022:
            return False
        parent = path.parent
        if parent.is_symlink() or not parent.is_dir():
            return False
        if parent.stat().st_mode & 0o022:
            return False
    except OSError:
        return False
    return True


def _keypair(directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    private = directory / "anchor.key"
    public = directory / "anchor.pub"
    subprocess.run(
        ["/usr/bin/openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["/usr/bin/openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)],
        check=True,
        capture_output=True,
    )
    os.chmod(private, 0o600)
    os.chmod(public, 0o644)
    return private, public


def _sign(private_key: Path, payload: dict) -> str:
    message = helper.canonical_payload_bytes(payload)
    with tempfile.TemporaryDirectory() as tmp:
        message_path = Path(tmp) / "msg"
        signature_path = Path(tmp) / "sig"
        message_path.write_bytes(message)
        completed = subprocess.run(
            [
                "/usr/bin/openssl",
                "pkeyutl",
                "-sign",
                "-inkey",
                str(private_key),
                "-rawin",
                "-in",
                str(message_path),
                "-out",
                str(signature_path),
            ],
            check=False,
            capture_output=True,
        )
        if completed.returncode != 0:
            _fail(f"openssl sign failed: {completed.stderr!r}")
        return base64.b64encode(signature_path.read_bytes()).decode("ascii")


def _repository(root: Path) -> tuple[Path, str]:
    repository = root / "repo"
    repository.mkdir()
    for relative in helper.TERMINAL_PATHS:
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"terminal {relative}\n", encoding="utf-8")
    _git(repository, "init", "-b", "main")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "terminal blobs")
    return repository, _git(repository, "rev-parse", "HEAD")


def _body(system_head: str, run_id: str = RUN_ID, lane: str = "CONTROL") -> dict:
    return {
        "descriptor_digest": "d" * 64,
        "effective_toolset": "write-shell-allowlist",
        "kind": "benchmark-host-receipt",
        "lane": lane,
        "lifecycle": "COMPLETE",
        "receipt_digest": "e" * 64,
        "repository": "datarelay-labs/engineering-system",
        "run_id": run_id,
        "schema_version": 1,
        "system_head": system_head,
        "task_source_head": "c" * 40,
        "telemetry_digest": "f" * 64,
    }


def _write_assertion(directory: Path, name: str, payload: dict) -> Path:
    path = directory / name
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    os.chmod(path, 0o644)
    os.chmod(directory, 0o755)
    return path


def _prepare(root: Path) -> dict:
    os.chmod(root, 0o755)
    repository, head = _repository(root)
    private, public = _keypair(root)
    os.chmod(public.parent, 0o755)
    receipt_dir = root / "receipts"
    receipt_dir.mkdir()
    os.chmod(receipt_dir, 0o755)
    payload = _body(head)
    signed = dict(payload)
    signed["signature"] = _sign(private, payload)
    assertion = _write_assertion(receipt_dir, f"{RUN_ID}-CONTROL.host-receipt.json", signed)
    boundary = helper.HostBoundary(
        helper=str(root / "helper"),
        anchor=str(public),
        private_key=str(private),
        receipt_dir=str(receipt_dir),
        openssl="/usr/bin/openssl",
        git="/usr/bin/git",
    )
    return {
        "repository": repository,
        "head": head,
        "private": private,
        "public": public,
        "receipt_dir": receipt_dir,
        "assertion": assertion,
        "payload": payload,
        "boundary": boundary,
    }


def _decide(prepared: dict, **overrides: object) -> str:
    return helper._decide_receipt(
        prepared["repository"] if "repository" not in overrides else overrides["repository"],  # type: ignore[arg-type]
        prepared["assertion"] if "assertion" not in overrides else overrides["assertion"],  # type: ignore[arg-type]
        prepared["public"] if "anchor" not in overrides else overrides["anchor"],  # type: ignore[arg-type]
        boundary=prepared["boundary"] if "boundary" not in overrides else overrides["boundary"],  # type: ignore[arg-type]
        provenance=helper.path_provenance if overrides.get("real_provenance") else _fixture_provenance,
    )


def test_contract_and_cli_do_not_touch_host_paths() -> None:
    before = {path: path.exists() for path in HOST_PATHS}
    completed = subprocess.run(
        [sys.executable, str(TOOLS / "benchmark_receipt_verify.py"), "contract"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        _fail(f"contract exited {completed.returncode}: {completed.stderr}")
    contract = json.loads(completed.stdout)
    if contract["worker_signing"] is not False or contract["private_key_mode"] != "0600":
        _fail(f"contract grants worker signing: {contract}")
    expected = {
        "helper": helper.HELPER_PATH,
        "anchor": helper.ANCHOR_PATH,
        "private_key": helper.PRIVATE_KEY_PATH,
        "receipt_dir": helper.RECEIPT_DIR,
        "openssl": helper.OPENSSL_PATH,
        "git": helper.GIT_PATH,
    }
    for key, value in expected.items():
        if contract[key] != value:
            _fail(f"contract {key} was {contract[key]}")
    source = (TOOLS / "benchmark_receipt_verify.py").read_text(encoding="utf-8")
    if "os.environ" in source or "genpkey" in source or "import skills" in source or "import persistent_benchmark" in source:
        _fail("helper imports caller environment or repository verifier code")
    for command in (
        ["decide", "/tmp/repo", "/tmp/assertion.json", "/tmp/anchor.pub"],
        ["sign", "/tmp/unsigned.json"],
        ["sign", "--key", "/tmp/worker.key", "/tmp/unsigned.json"],
        ["install"],
    ):
        probed = subprocess.run(
            [sys.executable, str(TOOLS / "benchmark_receipt_verify.py"), *command],
            check=False,
            capture_output=True,
            text=True,
        )
        if probed.returncode == 0:
            _fail(f"uninstalled helper accepted {command}")
    after = {path: path.exists() for path in HOST_PATHS}
    if after != before:
        _fail(f"helper CLI changed host paths from {before} to {after}")


def test_one_signed_receipt_qualifies_and_failures_block() -> None:
    sample = {"kind": "benchmark-host-receipt", "run_id": RUN_ID, "signature": "ignored"}
    if helper.canonical_payload_bytes(sample) != skills_contract.canonical_payload_bytes(sample):
        _fail("canonical payload bytes drifted from the skills contract")
    with tempfile.TemporaryDirectory() as temporary:
        prepared = _prepare(Path(temporary))
        if not skills_contract.ed25519_verify(
            prepared["public"],
            helper.canonical_payload_bytes(prepared["payload"]),
            json.loads(prepared["assertion"].read_text(encoding="utf-8"))["signature"],
        ):
            _fail("skills-contract rejected the fixture signature")
        if _decide(prepared, real_provenance=True) != "TRUST_BOUNDARY_UNAVAILABLE":
            _fail("real provenance accepted a same-uid fixture anchor")
        if _decide(prepared) != "":
            _fail(f"valid signed receipt returned {_decide(prepared)!r}")

        other = _body(prepared["head"], run_id=OTHER_RUN_ID)
        other_signed = dict(other)
        other_signed["signature"] = "not-a-signature"
        other_path = _write_assertion(
            prepared["receipt_dir"],
            f"{OTHER_RUN_ID}-CONTROL.host-receipt.json",
            other_signed,
        )
        if _decide(prepared, assertion=other_path) == "":
            _fail("a second unsigned receipt qualified")

        caller_key, caller_public = _keypair(Path(temporary) / "caller")
        os.chmod(caller_public.parent, 0o755)
        caller_signed = dict(prepared["payload"])
        caller_signed["signature"] = _sign(caller_key, prepared["payload"])
        caller_assertion = _write_assertion(
            prepared["receipt_dir"],
            f"{RUN_ID}-CANDIDATE.host-receipt.json",
            caller_signed | {"lane": "CANDIDATE"},
        )
        # The candidate body above was signed before the lane change. Rebuild it.
        candidate = _body(prepared["head"], lane="CANDIDATE")
        candidate_signed = dict(candidate)
        candidate_signed["signature"] = _sign(caller_key, candidate)
        caller_assertion.write_text(
            json.dumps(candidate_signed, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        if _decide(prepared, assertion=caller_assertion, anchor=caller_public) == "":
            _fail("caller-selected anchor qualified a receipt")
        if _decide(prepared, assertion=caller_assertion, anchor=caller_public) != "CALLER_TRUST_PATH":
            _fail("caller-selected anchor was not rejected as a trust path")

        outside = Path(temporary) / "outside.host-receipt.json"
        outside.write_text(prepared["assertion"].read_text(encoding="utf-8"), encoding="utf-8")
        if _decide(prepared, assertion=outside) != "CALLER_TRUST_PATH":
            _fail("caller-selected receipt path qualified")

        broken = json.loads(prepared["assertion"].read_text(encoding="utf-8"))
        broken["signature"] = broken["signature"][:-2] + ("A" if broken["signature"][-1:] != "A" else "B") + broken["signature"][-1]
        prepared["assertion"].write_text(json.dumps(broken, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        if _decide(prepared) != "SIGNATURE_MISMATCH":
            _fail(f"wrong signature returned {_decide(prepared)!r}")
        prepared["assertion"].write_text(
            json.dumps(
                {**prepared["payload"], "signature": _sign(prepared["private"], prepared["payload"])},
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

        wrong_head = _body("a" * 40)
        wrong_signed = dict(wrong_head)
        wrong_signed["signature"] = _sign(prepared["private"], wrong_head)
        prepared["assertion"].write_text(json.dumps(wrong_signed, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        if _decide(prepared) != "HEAD_MISMATCH":
            _fail(f"wrong head returned {_decide(prepared)!r}")
        prepared["assertion"].write_text(
            json.dumps(
                {**prepared["payload"], "signature": _sign(prepared["private"], prepared["payload"])},
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

        target = prepared["repository"] / "tools" / "persistent_benchmark_telemetry.py"
        target.write_text("replaced terminal code\n", encoding="utf-8")
        if _decide(prepared) != "BLOB_MISMATCH":
            _fail(f"replaced blob returned {_decide(prepared)!r}")
        target.write_text("terminal tools/persistent_benchmark_telemetry.py\n", encoding="utf-8")
        if _decide(prepared) != "":
            _fail("restored blob did not qualify again")

        (prepared["repository"] / "untracked.txt").write_text("dirty\n", encoding="utf-8")
        if _decide(prepared) != "TREE_DIRTY":
            _fail(f"dirty tree returned {_decide(prepared)!r}")
        (prepared["repository"] / "untracked.txt").unlink()
        if _decide(prepared) != "":
            _fail("clean tree did not qualify again")

        prepared["assertion"].chmod(0o666)
        if _decide(prepared) != "TRUST_BOUNDARY_UNAVAILABLE":
            _fail("group-writable receipt qualified")


def test_production_provenance_rejects_same_uid_files() -> None:
    if not helper.path_provenance(Path("/usr/bin/openssl"), expect_file=True):
        _fail("fixed openssl failed provenance")
    if not helper.path_provenance(Path("/usr/bin/git"), expect_file=True):
        _fail("fixed git failed provenance")
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "anchor.pub"
        path.write_text("not-root\n", encoding="utf-8")
        os.chmod(path, 0o644)
        if helper.path_provenance(path, expect_file=True):
            _fail("same-uid anchor passed provenance")
        link = Path(temporary) / "link.pub"
        link.symlink_to(path)
        if helper.path_provenance(link, expect_file=True):
            _fail("symlink anchor passed provenance")
    if helper.path_provenance(Path(helper.HELPER_PATH), expect_file=True):
        _fail("absent helper passed provenance")
    if helper.path_provenance(Path(helper.ANCHOR_PATH), expect_file=True):
        _fail("absent anchor passed provenance")
    mode = stat.S_IMODE(Path("/usr/bin/openssl").stat().st_mode)
    if mode & 0o022:
        _fail("openssl provenance fixture is group or world writable")


def main() -> None:
    test_contract_and_cli_do_not_touch_host_paths()
    test_production_provenance_rejects_same_uid_files()
    test_one_signed_receipt_qualifies_and_failures_block()
    print("PASS benchmark receipt verify")


if __name__ == "__main__":
    main()
