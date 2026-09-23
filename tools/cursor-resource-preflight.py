#!/usr/bin/env python3
"""Fail closed before creating a new Cursor persistent session.

This tool only reads host memory, swap, and `agent persist list`. It never
stops, kills, attaches to, or otherwise mutates existing Cursor sessions.

Exit status:
  0  PASS or WARN; a new persistent session may proceed
  2  BLOCK because a resource threshold was exceeded
  3  BLOCK because facts or the host override could not be trusted
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

GIB = 1024**3
MIB = 1024**2
LARGE_MIN_BYTES = 24 * GIB
MEDIUM_MIN_BYTES = 8 * GIB
ALLOWED_OVERRIDE_KEYS = frozenset(
    {
        "profile",
        "block_mem_available_bytes",
        "block_swap_used_bytes",
        "warn_sessions",
        "block_sessions",
    }
)
KNOWN_PROFILES = frozenset({"large", "medium", "small"})
PERSIST_HEADER_RE = re.compile(r"(?m)^(\d+) persistent sessions?:")
PERSIST_SESSION_RE = re.compile(r"(?m)^[ \t]*Session:[ \t]*\S+")
NO_SESSIONS_RE = re.compile(r"(?i)no persistent sessions")
REPORT_KEYS = (
    "RESULT",
    "EXIT_CODE",
    "REASON",
    "PROFILE",
    "PROFILE_SOURCE",
    "MEM_TOTAL_BYTES",
    "MEM_AVAILABLE_BYTES",
    "SWAP_TOTAL_BYTES",
    "SWAP_USED_BYTES",
    "SWAP_CONFIGURED",
    "SESSION_COUNT",
    "BLOCK_MEM_AVAILABLE_BYTES",
    "BLOCK_SWAP_USED_BYTES",
    "WARN_SESSIONS",
    "BLOCK_SESSIONS",
)


class PreflightFailure(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def parse_meminfo(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for raw in text.splitlines():
        if ":" not in raw:
            continue
        key, rest = raw.split(":", 1)
        parts = rest.split()
        if not parts:
            continue
        try:
            number = int(parts[0])
        except ValueError as exc:
            raise PreflightFailure(f"unreadable meminfo field {key}") from exc
        if len(parts) > 1 and parts[1] == "kB":
            number *= 1024
        values[key.strip()] = number
    for required in ("MemTotal", "MemAvailable", "SwapTotal"):
        if required not in values:
            raise PreflightFailure(f"meminfo missing {required}")
    if values["MemTotal"] <= 0:
        raise PreflightFailure("meminfo MemTotal is not positive")
    if values["MemAvailable"] < 0:
        raise PreflightFailure("meminfo MemAvailable is negative")
    if values["SwapTotal"] < 0:
        raise PreflightFailure("meminfo SwapTotal is negative")
    if values["SwapTotal"] > 0 and "SwapFree" not in values:
        raise PreflightFailure("meminfo missing SwapFree")
    if values.get("SwapFree", 0) < 0:
        raise PreflightFailure("meminfo SwapFree is negative")
    return values


def parse_persist_list(text: str) -> int:
    header = PERSIST_HEADER_RE.search(text)
    sessions = PERSIST_SESSION_RE.findall(text)
    if header:
        count = int(header.group(1))
        if count != len(sessions):
            raise PreflightFailure("agent persist list session count does not match Session rows")
        return count
    if NO_SESSIONS_RE.search(text):
        if sessions:
            raise PreflightFailure("agent persist list reported no sessions but included Session rows")
        return 0
    raise PreflightFailure("agent persist list output is unparseable")


def select_profile(mem_total: int) -> str:
    if mem_total >= LARGE_MIN_BYTES:
        return "large"
    if mem_total >= MEDIUM_MIN_BYTES:
        return "medium"
    return "small"


def _bounded_mem_block(mem_total: int, preferred: int) -> int:
    block = preferred
    if block <= 0 or block >= mem_total:
        block = mem_total // 2
    if block <= 0 or block >= mem_total:
        raise PreflightFailure("profile cannot derive a valid MemAvailable threshold for this host")
    return block


def builtin_thresholds(profile: str, mem_total: int) -> dict[str, int | str]:
    if profile not in KNOWN_PROFILES:
        raise PreflightFailure(f"unknown resource profile {profile}")
    if profile == "large":
        block_mem = 8 * GIB
        block_swap = 1 * GIB
        warn_sessions = 6
        block_sessions = 8
    elif profile == "medium":
        block_mem = max(1 * GIB, mem_total // 4)
        block_swap = 1 * GIB
        warn_sessions = 6
        block_sessions = 8
    else:
        block_mem = _bounded_mem_block(mem_total, max(64 * MIB, mem_total // 5))
        block_swap = 256 * MIB
        warn_sessions = 2
        block_sessions = 4
    return {
        "profile": profile,
        "block_mem_available_bytes": block_mem,
        "block_swap_used_bytes": block_swap,
        "warn_sessions": warn_sessions,
        "block_sessions": block_sessions,
    }


def _require_int(value: object, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PreflightFailure(f"resource override {key} must be an integer")
    return value


def load_override(text: str) -> dict[str, object]:
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PreflightFailure("resource override is malformed YAML") from exc
    if loaded is None:
        raise PreflightFailure("resource override is empty")
    if not isinstance(loaded, dict):
        raise PreflightFailure("resource override must be a mapping")
    unknown = sorted(set(loaded) - ALLOWED_OVERRIDE_KEYS)
    if unknown:
        raise PreflightFailure("resource override contains unknown keys: " + ", ".join(unknown))
    return loaded


def apply_override(mem_total: int, override: dict[str, object] | None, source: str) -> dict[str, int | str]:
    profile = select_profile(mem_total)
    if override and "profile" in override:
        requested = override["profile"]
        if not isinstance(requested, str) or requested not in KNOWN_PROFILES:
            raise PreflightFailure("resource override profile must be large, medium, or small")
        profile = requested
        source = "override"
    thresholds = builtin_thresholds(profile, mem_total)
    if not override:
        thresholds["profile_source"] = "builtin"
        _validate_thresholds(thresholds, mem_total)
        return thresholds
    for key in ("block_mem_available_bytes", "block_swap_used_bytes", "warn_sessions", "block_sessions"):
        if key in override:
            thresholds[key] = _require_int(override[key], key)
            source = "override"
    thresholds["profile_source"] = source
    _validate_thresholds(thresholds, mem_total)
    return thresholds


def _validate_thresholds(thresholds: dict[str, int | str], mem_total: int) -> None:
    block_mem = int(thresholds["block_mem_available_bytes"])
    block_swap = int(thresholds["block_swap_used_bytes"])
    warn_sessions = int(thresholds["warn_sessions"])
    block_sessions = int(thresholds["block_sessions"])
    if block_mem <= 0 or block_mem >= mem_total:
        raise PreflightFailure("MemAvailable block threshold is impossible for this host")
    if block_swap < 0:
        raise PreflightFailure("swap block threshold is negative")
    if warn_sessions < 1 or block_sessions < 1:
        raise PreflightFailure("session thresholds must be positive")
    if warn_sessions >= block_sessions:
        raise PreflightFailure("warning session threshold must be below the block threshold")


def evaluate(meminfo: dict[str, int], sessions: int, thresholds: dict[str, int | str]) -> dict[str, str]:
    mem_total = meminfo["MemTotal"]
    mem_available = meminfo["MemAvailable"]
    swap_total = meminfo["SwapTotal"]
    swap_free = meminfo.get("SwapFree", 0)
    swap_used = max(0, swap_total - swap_free) if swap_total > 0 else 0
    blocks: list[str] = []
    warns: list[str] = []
    block_mem = int(thresholds["block_mem_available_bytes"])
    block_swap = int(thresholds["block_swap_used_bytes"])
    warn_sessions = int(thresholds["warn_sessions"])
    block_sessions = int(thresholds["block_sessions"])
    if mem_available < block_mem:
        blocks.append(
            f"MemAvailable {mem_available} bytes is below block threshold {block_mem} bytes"
        )
    if swap_total > 0 and swap_used > block_swap:
        blocks.append(f"swap used {swap_used} bytes exceeds block threshold {block_swap} bytes")
    if sessions >= block_sessions:
        blocks.append(f"persistent sessions {sessions} reached block threshold {block_sessions}")
    elif sessions >= warn_sessions:
        warns.append(f"persistent sessions {sessions} reached warning threshold {warn_sessions}")
    if blocks:
        result = "BLOCK"
        exit_code = 2
        reason = "; ".join(blocks)
    elif warns:
        result = "WARN"
        exit_code = 0
        reason = "; ".join(warns)
    else:
        result = "PASS"
        exit_code = 0
        reason = "host is within resource thresholds"
    return {
        "RESULT": result,
        "EXIT_CODE": str(exit_code),
        "REASON": reason,
        "PROFILE": str(thresholds["profile"]),
        "PROFILE_SOURCE": str(thresholds["profile_source"]),
        "MEM_TOTAL_BYTES": str(mem_total),
        "MEM_AVAILABLE_BYTES": str(mem_available),
        "SWAP_TOTAL_BYTES": str(swap_total),
        "SWAP_USED_BYTES": str(swap_used),
        "SWAP_CONFIGURED": "YES" if swap_total > 0 else "NO",
        "SESSION_COUNT": str(sessions),
        "BLOCK_MEM_AVAILABLE_BYTES": str(block_mem),
        "BLOCK_SWAP_USED_BYTES": str(block_swap),
        "WARN_SESSIONS": str(warn_sessions),
        "BLOCK_SESSIONS": str(block_sessions),
    }


def format_report(fields: dict[str, str]) -> str:
    lines = []
    for key in REPORT_KEYS:
        if key not in fields:
            continue
        value = fields[key].replace("\n", " ").strip()
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def failure_report(reason: str) -> tuple[str, int]:
    return format_report({"RESULT": "BLOCK", "EXIT_CODE": "3", "REASON": reason}), 3


def read_persist_list(agent_bin: str) -> str:
    try:
        completed = subprocess.run(
            [agent_bin, "persist", "list"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=15,
        )
    except FileNotFoundError as exc:
        raise PreflightFailure(f"agent persist list unavailable: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PreflightFailure("agent persist list timed out") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        suffix = f": {detail[0]}" if detail else ""
        raise PreflightFailure(f"agent persist list failed{suffix}")
    return completed.stdout


def discover_config(explicit: str | None, host_config: bool) -> Path | None:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise PreflightFailure(f"resource override missing: {path}")
        return path
    if not host_config:
        return None
    env_path = os.environ.get("ENGINEERING_SYSTEM_CURSOR_RESOURCE_GUARD", "").strip()
    if env_path:
        path = Path(env_path)
        if not path.is_file():
            raise PreflightFailure(f"resource override missing: {path}")
        return path
    candidates: list[Path] = []
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        candidates.append(Path(xdg) / "engineering-system" / "cursor-resource-guard.yaml")
    candidates.append(Path.home() / ".config" / "engineering-system" / "cursor-resource-guard.yaml")
    candidates.append(Path("/etc/engineering-system/cursor-resource-guard.yaml"))
    for path in candidates:
        if path.is_file():
            return path
    return None


def run_preflight(args: argparse.Namespace) -> int:
    try:
        if not args.meminfo_file and not sys.platform.startswith("linux"):
            raise PreflightFailure(f"platform unsupported: {sys.platform}")
        meminfo_text = (
            Path(args.meminfo_file).read_text(encoding="utf-8")
            if args.meminfo_file
            else Path("/proc/meminfo").read_text(encoding="utf-8")
        )
        meminfo = parse_meminfo(meminfo_text)
        config_path = discover_config(args.config, host_config=not args.no_host_config)
        override = load_override(config_path.read_text(encoding="utf-8")) if config_path else None
        source = "override" if config_path else "builtin"
        thresholds = apply_override(meminfo["MemTotal"], override, source)
        if args.persist_list_file:
            persist_text = Path(args.persist_list_file).read_text(encoding="utf-8")
        else:
            persist_text = read_persist_list(args.agent_bin)
        sessions = parse_persist_list(persist_text)
        report = evaluate(meminfo, sessions, thresholds)
    except OSError as exc:
        report_text, code = failure_report(f"resource facts unreadable: {exc.strerror or exc}")
        sys.stdout.write(report_text)
        return code
    except PreflightFailure as exc:
        report_text, code = failure_report(exc.reason)
        sys.stdout.write(report_text)
        return code
    sys.stdout.write(format_report(report))
    return int(report["EXIT_CODE"])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meminfo-file", help="Read memory facts from this file instead of /proc/meminfo")
    parser.add_argument("--persist-list-file", help="Read agent persist list output from this file")
    parser.add_argument("--config", help="Host override YAML. Missing file fails closed")
    parser.add_argument("--agent-bin", default=os.environ.get("CURSOR_AGENT_BIN", "agent"))
    parser.add_argument(
        "--no-host-config",
        action="store_true",
        help="Use the builtin profile only and ignore host override files",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.config and args.no_host_config:
        sys.stdout.write(failure_report("pass only one of --config and --no-host-config")[0])
        return 3
    return run_preflight(args)


if __name__ == "__main__":
    sys.exit(main())
