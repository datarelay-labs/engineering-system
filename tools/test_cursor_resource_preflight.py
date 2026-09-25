#!/usr/bin/env python3
"""Deterministic regressions for the Cursor persistent-session resource guard."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "cursor-resource-preflight.py"
GIB = 1024**3
MIB = 1024**2

_SPEC = importlib.util.spec_from_file_location("cursor_resource_preflight", TOOL)
assert _SPEC and _SPEC.loader
preflight = importlib.util.module_from_spec(_SPEC)
sys.modules["cursor_resource_preflight"] = preflight
_SPEC.loader.exec_module(preflight)


def meminfo(total: int, available: int, swap_total: int = 0, swap_free: int | None = None) -> str:
    lines = [
        f"MemTotal: {total // 1024} kB",
        f"MemAvailable: {available // 1024} kB",
        f"SwapTotal: {swap_total // 1024} kB",
    ]
    if swap_total > 0:
        free = swap_total if swap_free is None else swap_free
        lines.append(f"SwapFree: {free // 1024} kB")
    elif swap_free is not None:
        lines.append(f"SwapFree: {swap_free // 1024} kB")
    return "\n".join(lines) + "\n"


def persist_list(count: int) -> str:
    lines = [f"{count} persistent sessions:"]
    for index in range(count):
        lines.append(f"  Session: cursor-test-{index}")
    return "\n".join(lines) + "\n"


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.pop("ENGINEERING_SYSTEM_CURSOR_RESOURCE_GUARD", None)
    merged.pop(preflight.RESOURCE_GUARD_CONFIG_ENV, None)
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=merged,
        check=False,
    )


def fields(stdout: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in stdout.splitlines():
        key, value = line.split("=", 1)
        parsed[key] = value
    return parsed


def test_healthy_large_host_passes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        mem = base / "meminfo"
        sessions = base / "sessions"
        mem.write_text(meminfo(29 * GIB, 20 * GIB, 4 * GIB, 4 * GIB), encoding="utf-8")
        sessions.write_text(persist_list(2), encoding="utf-8")
        result = run_cli("--meminfo-file", str(mem), "--persist-list-file", str(sessions), "--no-host-config")
    assert result.returncode == 0, result.stdout
    report = fields(result.stdout)
    assert report["RESULT"] == "PASS"
    assert report["PROFILE"] == "large"
    assert report["BLOCK_MEM_AVAILABLE_BYTES"] == str(8 * GIB)
    assert report["BLOCK_SWAP_USED_BYTES"] == str(1 * GIB)
    assert report["WARN_SESSIONS"] == "6"
    assert report["BLOCK_SESSIONS"] == "8"


def test_warning_session_count_is_allowed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        mem = base / "meminfo"
        sessions = base / "sessions"
        mem.write_text(meminfo(29 * GIB, 20 * GIB, 4 * GIB, 4 * GIB), encoding="utf-8")
        sessions.write_text(persist_list(6), encoding="utf-8")
        result = run_cli("--meminfo-file", str(mem), "--persist-list-file", str(sessions), "--no-host-config")
    assert result.returncode == 0, result.stdout
    assert fields(result.stdout)["RESULT"] == "WARN"


def test_session_block_threshold() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        mem = base / "meminfo"
        sessions = base / "sessions"
        mem.write_text(meminfo(29 * GIB, 20 * GIB, 4 * GIB, 4 * GIB), encoding="utf-8")
        sessions.write_text(persist_list(8), encoding="utf-8")
        result = run_cli("--meminfo-file", str(mem), "--persist-list-file", str(sessions), "--no-host-config")
    assert result.returncode == 2, result.stdout
    assert fields(result.stdout)["RESULT"] == "BLOCK"


def test_low_mem_available_blocks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        mem = base / "meminfo"
        sessions = base / "sessions"
        mem.write_text(meminfo(29 * GIB, 1 * GIB, 4 * GIB, 4 * GIB), encoding="utf-8")
        sessions.write_text(persist_list(1), encoding="utf-8")
        result = run_cli("--meminfo-file", str(mem), "--persist-list-file", str(sessions), "--no-host-config")
    assert result.returncode == 2, result.stdout
    report = fields(result.stdout)
    assert report["RESULT"] == "BLOCK"
    assert "MemAvailable" in report["REASON"]


def test_excessive_swap_blocks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        mem = base / "meminfo"
        sessions = base / "sessions"
        mem.write_text(meminfo(29 * GIB, 20 * GIB, 4 * GIB, 2 * GIB), encoding="utf-8")
        sessions.write_text(persist_list(1), encoding="utf-8")
        result = run_cli("--meminfo-file", str(mem), "--persist-list-file", str(sessions), "--no-host-config")
    assert result.returncode == 2, result.stdout
    assert "swap used" in fields(result.stdout)["REASON"]


def test_no_swap_does_not_block() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        mem = base / "meminfo"
        sessions = base / "sessions"
        mem.write_text(meminfo(29 * GIB, 20 * GIB, 0, 0), encoding="utf-8")
        sessions.write_text(persist_list(1), encoding="utf-8")
        result = run_cli("--meminfo-file", str(mem), "--persist-list-file", str(sessions), "--no-host-config")
    assert result.returncode == 0, result.stdout
    report = fields(result.stdout)
    assert report["RESULT"] == "PASS"
    assert report["SWAP_CONFIGURED"] == "NO"


def test_small_host_profile_is_possible() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        mem = base / "meminfo"
        sessions = base / "sessions"
        mem.write_text(meminfo(1 * GIB, 700 * MIB), encoding="utf-8")
        sessions.write_text(persist_list(0), encoding="utf-8")
        result = run_cli("--meminfo-file", str(mem), "--persist-list-file", str(sessions), "--no-host-config")
    assert result.returncode == 0, result.stdout
    report = fields(result.stdout)
    assert report["RESULT"] == "PASS"
    assert report["PROFILE"] == "small"
    block_mem = int(report["BLOCK_MEM_AVAILABLE_BYTES"])
    assert block_mem < 8 * GIB
    assert block_mem < 1 * GIB


def isolated_config_env(base: Path, **extra: str) -> dict[str, str]:
    home = base / "home"
    xdg = base / "xdg"
    home.mkdir(parents=True)
    xdg.mkdir(parents=True)
    values = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(xdg),
        "ENGINEERING_SYSTEM_CURSOR_RESOURCE_GUARD": str(TOOL),
    }
    values.update(extra)
    return values


def test_executable_path_env_is_not_parsed_as_yaml() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        mem = base / "meminfo"
        sessions = base / "sessions"
        mem.write_text(meminfo(29 * GIB, 20 * GIB, 4 * GIB, 4 * GIB), encoding="utf-8")
        sessions.write_text(persist_list(1), encoding="utf-8")
        common = ("--meminfo-file", str(mem), "--persist-list-file", str(sessions))
        collided = run_cli(*common, env=isolated_config_env(base))
        baseline = run_cli(*common, env=isolated_config_env(base / "baseline"))
    assert collided.returncode == baseline.returncode, collided.stdout
    assert collided.returncode == 0, collided.stdout
    report = fields(collided.stdout)
    assert report["RESULT"] == "PASS"
    assert report["PROFILE_SOURCE"] == "builtin"
    assert report["BLOCK_MEM_AVAILABLE_BYTES"] == str(8 * GIB)
    assert "malformed" not in report["REASON"]
    assert fields(baseline.stdout)["PROFILE_SOURCE"] == "builtin"


def test_explicit_config_env_overrides_thresholds() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        mem = base / "meminfo"
        sessions = base / "sessions"
        config = base / "guard.yaml"
        mem.write_text(meminfo(29 * GIB, 20 * GIB, 4 * GIB, 4 * GIB), encoding="utf-8")
        sessions.write_text(persist_list(3), encoding="utf-8")
        config.write_text("warn_sessions: 2\nblock_sessions: 3\n", encoding="utf-8")
        result = run_cli(
            "--meminfo-file",
            str(mem),
            "--persist-list-file",
            str(sessions),
            env=isolated_config_env(base, **{preflight.RESOURCE_GUARD_CONFIG_ENV: str(config)}),
        )
    assert result.returncode == 2, result.stdout
    report = fields(result.stdout)
    assert report["PROFILE_SOURCE"] == "override"
    assert report["BLOCK_SESSIONS"] == "3"
    assert report["BLOCK_MEM_AVAILABLE_BYTES"] == str(8 * GIB)
    assert report["RESULT"] == "BLOCK"


def test_default_user_yaml_still_discovered() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        mem = base / "meminfo"
        sessions = base / "sessions"
        mem.write_text(meminfo(29 * GIB, 20 * GIB, 4 * GIB, 4 * GIB), encoding="utf-8")
        sessions.write_text(persist_list(3), encoding="utf-8")
        env = isolated_config_env(base)
        config_dir = Path(env["XDG_CONFIG_HOME"]) / "engineering-system"
        config_dir.mkdir()
        (config_dir / "cursor-resource-guard.yaml").write_text(
            "warn_sessions: 2\nblock_sessions: 3\n",
            encoding="utf-8",
        )
        result = run_cli(
            "--meminfo-file",
            str(mem),
            "--persist-list-file",
            str(sessions),
            env=env,
        )
    assert result.returncode == 2, result.stdout
    report = fields(result.stdout)
    assert report["PROFILE_SOURCE"] == "override"
    assert report["BLOCK_SESSIONS"] == "3"


def test_missing_explicit_config_env_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        mem = base / "meminfo"
        sessions = base / "sessions"
        mem.write_text(meminfo(29 * GIB, 20 * GIB), encoding="utf-8")
        sessions.write_text(persist_list(0), encoding="utf-8")
        result = run_cli(
            "--meminfo-file",
            str(mem),
            "--persist-list-file",
            str(sessions),
            env=isolated_config_env(
                base,
                **{preflight.RESOURCE_GUARD_CONFIG_ENV: str(base / "missing.yaml")},
            ),
        )
    assert result.returncode == 3, result.stdout
    report = fields(result.stdout)
    assert report["RESULT"] == "BLOCK"
    assert "missing" in report["REASON"]


def test_malformed_explicit_config_env_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        mem = base / "meminfo"
        sessions = base / "sessions"
        mem.write_text(meminfo(29 * GIB, 20 * GIB), encoding="utf-8")
        sessions.write_text(persist_list(0), encoding="utf-8")
        result = run_cli(
            "--meminfo-file",
            str(mem),
            "--persist-list-file",
            str(sessions),
            env=isolated_config_env(base, **{preflight.RESOURCE_GUARD_CONFIG_ENV: str(TOOL)}),
        )
    assert result.returncode == 3, result.stdout
    report = fields(result.stdout)
    assert report["RESULT"] == "BLOCK"
    assert "malformed" in report["REASON"]


def test_resource_guard_names_agree() -> None:
    executable_env = "ENGINEERING_SYSTEM_CURSOR_RESOURCE_GUARD"
    config_env = preflight.RESOURCE_GUARD_CONFIG_ENV
    resumes = [
        ROOT / ".cursor/commands/resume.md",
        ROOT / ".cursor/commands/work-resume.md",
        ROOT / "templates/.cursor/commands/resume.md",
        ROOT / "templates/.cursor/commands/work-resume.md",
    ]
    resume_text = resumes[0].read_text(encoding="utf-8")
    for path in resumes[1:]:
        assert path.read_text(encoding="utf-8") == resume_text
    session = (ROOT / "standards/SESSION_CONTINUITY.md").read_text(encoding="utf-8")
    instruction = (ROOT / "templates/CHATGPT_CUSTOM_INSTRUCTION.txt").read_text(encoding="utf-8")
    tool = TOOL.read_text(encoding="utf-8")
    for text in (resume_text, session, instruction, tool):
        assert executable_env in text
        assert config_env in text
    assert "never threshold YAML" in resume_text
    assert "never parsed as YAML" in session
    assert "never threshold YAML" in instruction
    assert 'os.environ.get("ENGINEERING_SYSTEM_CURSOR_RESOURCE_GUARD"' not in tool
    assert "os.environ.get(RESOURCE_GUARD_CONFIG_ENV" in tool


def test_host_override_precedes_builtin_profile() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        mem = base / "meminfo"
        sessions = base / "sessions"
        config = base / "guard.yaml"
        mem.write_text(meminfo(29 * GIB, 20 * GIB, 4 * GIB, 4 * GIB), encoding="utf-8")
        sessions.write_text(persist_list(3), encoding="utf-8")
        config.write_text("warn_sessions: 2\nblock_sessions: 3\n", encoding="utf-8")
        result = run_cli(
            "--meminfo-file",
            str(mem),
            "--persist-list-file",
            str(sessions),
            "--config",
            str(config),
        )
    assert result.returncode == 2, result.stdout
    report = fields(result.stdout)
    assert report["PROFILE_SOURCE"] == "override"
    assert report["BLOCK_SESSIONS"] == "3"
    assert report["RESULT"] == "BLOCK"


def test_malformed_override_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        mem = base / "meminfo"
        sessions = base / "sessions"
        config = base / "guard.yaml"
        mem.write_text(meminfo(29 * GIB, 20 * GIB), encoding="utf-8")
        sessions.write_text(persist_list(0), encoding="utf-8")
        config.write_text("kill_sessions: true\n", encoding="utf-8")
        result = run_cli(
            "--meminfo-file",
            str(mem),
            "--persist-list-file",
            str(sessions),
            "--config",
            str(config),
        )
    assert result.returncode == 3, result.stdout
    report = fields(result.stdout)
    assert report["RESULT"] == "BLOCK"
    assert "unknown keys" in report["REASON"]


def test_impossible_override_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        mem = base / "meminfo"
        sessions = base / "sessions"
        config = base / "guard.yaml"
        mem.write_text(meminfo(1 * GIB, 700 * MIB), encoding="utf-8")
        sessions.write_text(persist_list(0), encoding="utf-8")
        config.write_text("profile: large\n", encoding="utf-8")
        result = run_cli(
            "--meminfo-file",
            str(mem),
            "--persist-list-file",
            str(sessions),
            "--config",
            str(config),
        )
    assert result.returncode == 3, result.stdout
    assert "impossible" in fields(result.stdout)["REASON"]


def test_unparseable_persist_list_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        mem = base / "meminfo"
        sessions = base / "sessions"
        mem.write_text(meminfo(29 * GIB, 20 * GIB), encoding="utf-8")
        sessions.write_text("not a session list\n", encoding="utf-8")
        result = run_cli("--meminfo-file", str(mem), "--persist-list-file", str(sessions), "--no-host-config")
    assert result.returncode == 3, result.stdout
    assert fields(result.stdout)["RESULT"] == "BLOCK"


def test_parse_persist_list_accepts_cursor_managed_zero_session_wording() -> None:
    assert preflight.parse_persist_list("No Cursor-managed persistent sessions.\n") == 0
    assert preflight.parse_persist_list("no persistent sessions\n") == 0
    assert preflight.parse_persist_list("0 persistent sessions:\n") == 0


def test_parse_persist_list_rejects_unexpected_zero_session_variant() -> None:
    try:
        preflight.parse_persist_list("No unexpected words persistent sessions.\n")
    except preflight.PreflightFailure as exc:
        assert "unparseable" in str(exc)
    else:
        raise AssertionError("unexpected zero-session variant was accepted")


def test_parse_persist_list_rejects_affixed_zero_session_line() -> None:
    for text in (
        "prefix No Cursor-managed persistent sessions.\n",
        "No Cursor-managed persistent sessions. trailing\n",
        "note: no persistent sessions\n",
    ):
        try:
            preflight.parse_persist_list(text)
        except preflight.PreflightFailure as exc:
            assert "unparseable" in str(exc), text
        else:
            raise AssertionError(f"affixed zero-session line was accepted: {text!r}")


def test_parse_persist_list_rejects_no_session_text_with_session_rows() -> None:
    contradictory = "No Cursor-managed persistent sessions.\n  Session: cursor-test-0\n"
    try:
        preflight.parse_persist_list(contradictory)
    except preflight.PreflightFailure as exc:
        assert "no sessions but included Session rows" in str(exc)
    else:
        raise AssertionError("contradictory zero-session output was accepted")


def test_missing_agent_fails_closed_without_stopping_sessions() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        mem = base / "meminfo"
        record = base / "record"
        agent = base / "agent"
        mem.write_text(meminfo(29 * GIB, 20 * GIB), encoding="utf-8")
        agent.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$1 $2\" >> {record}\n"
            "echo '0 persistent sessions:'\n",
            encoding="utf-8",
        )
        agent.chmod(0o755)
        listed = run_cli(
            "--meminfo-file",
            str(mem),
            "--agent-bin",
            str(agent),
            "--no-host-config",
        )
        missing = run_cli(
            "--meminfo-file",
            str(mem),
            "--agent-bin",
            str(base / "missing-agent"),
            "--no-host-config",
        )
        assert listed.returncode == 0, listed.stdout
        assert record.read_text(encoding="utf-8").strip() == "persist list"
        assert missing.returncode == 3, missing.stdout
        assert "unavailable" in fields(missing.stdout)["REASON"]


def test_tool_never_encodes_session_mutation() -> None:
    source = TOOL.read_text(encoding="utf-8")
    for forbidden in ("persist stop", "os.kill", "pkill", "killall", "SIGKILL", "SIGTERM"):
        assert forbidden not in source


def test_chatgpt_handoff_requires_preflight_before_persist() -> None:
    text = (ROOT / "templates" / "CHATGPT_CUSTOM_INSTRUCTION.txt").read_text(encoding="utf-8")
    preflight_at = text.index("tools/cursor-resource-preflight.py")
    persist_at = text.index("agent persist /work-resume")
    assert preflight_at < persist_at
    assert "Exit 0" in text
    assert "WARN" in text
    assert "do not create a new persistent session" in text
    assert "Never stop, kill" in text


def test_known_resume_upgrades_and_custom_resume_fails_closed() -> None:
    upgrade_path = ROOT / "tools" / "upgrade-adoption.py"
    spec = importlib.util.spec_from_file_location("upgrade_adoption", upgrade_path)
    assert spec and spec.loader
    upgrade = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upgrade)
    canonical = (ROOT / "templates" / ".cursor" / "commands" / "resume.md").read_text(encoding="utf-8")
    prior = (ROOT / "tools" / "managed_adapter_history" / "resume" / "1.6.4.md").read_text(encoding="utf-8")
    assert prior != canonical
    assert "cursor-resource-preflight.py" in canonical
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)
        resume = target / ".cursor" / "commands" / "resume.md"
        resume.parent.mkdir(parents=True)
        resume.write_text(prior, encoding="utf-8")
        assert upgrade.plan_cursor_resume_adapters(target)[".cursor/commands/resume.md"] == canonical
        resume.write_text("# project-custom resume\n", encoding="utf-8")
        try:
            upgrade.plan_cursor_resume_adapters(target)
        except SystemExit as exc:
            assert "local/custom changes" in str(exc)
        else:
            raise AssertionError("custom resume was accepted")
        assert resume.read_text(encoding="utf-8") == "# project-custom resume\n"


def main() -> int:
    subprocess.run([sys.executable, "-m", "py_compile", str(TOOL)], check=True)
    test_healthy_large_host_passes()
    test_warning_session_count_is_allowed()
    test_session_block_threshold()
    test_low_mem_available_blocks()
    test_excessive_swap_blocks()
    test_no_swap_does_not_block()
    test_small_host_profile_is_possible()
    test_executable_path_env_is_not_parsed_as_yaml()
    test_explicit_config_env_overrides_thresholds()
    test_default_user_yaml_still_discovered()
    test_missing_explicit_config_env_fails_closed()
    test_malformed_explicit_config_env_fails_closed()
    test_resource_guard_names_agree()
    test_host_override_precedes_builtin_profile()
    test_malformed_override_fails_closed()
    test_impossible_override_fails_closed()
    test_unparseable_persist_list_fails_closed()
    test_parse_persist_list_accepts_cursor_managed_zero_session_wording()
    test_parse_persist_list_rejects_unexpected_zero_session_variant()
    test_parse_persist_list_rejects_affixed_zero_session_line()
    test_parse_persist_list_rejects_no_session_text_with_session_rows()
    test_missing_agent_fails_closed_without_stopping_sessions()
    test_tool_never_encodes_session_mutation()
    test_chatgpt_handoff_requires_preflight_before_persist()
    test_known_resume_upgrades_and_custom_resume_fails_closed()
    print("CURSOR_RESOURCE_PREFLIGHT_TESTS=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
