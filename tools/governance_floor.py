#!/usr/bin/env python3
"""Base-owned fail-closed governance floor for Engineering System pull requests."""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

import yaml

FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MANAGED_EXECUTION_SURFACES = (
    "AGENTS.md",
    "tools/context_epoch.py",
    "tools/engineering-context.py",
    "tools/governance_floor.py",
)
RETIRED_AGENT_ARTIFACTS = (".cursor", ".cursorignore", ".cursorrules")
RETIRED_EXECUTION_PATTERNS = {
    "AGENTS.md": (
        "owner explicitly reactivates it for the current Work Packet with `IMPLEMENTER=CURSOR`",
        "Cursor adapter is disabled by default",
    ),
    "tools/context_epoch.py": (
        'implementer not in {"CHATGPT_CHAT", "CURSOR"}',
        "implementer not in {'CHATGPT_CHAT', 'CURSOR'}",
    ),
    "tools/engineering-context.py": (
        ".cursor/rules/engineering-system.mdc",
    ),
}
GOVERNANCE_HELPER = "tools/governance_floor.py"
ENGINEERING_WORKFLOW = ".github/workflows/engineering-system.yml"
REQUIRED_WORKFLOW_TOKENS = (
    "pull_request_target:",
    "governance-floor:",
    "github.event_name == 'pull_request_target'",
    "datarelay-labs/engineering-system/.github/workflows/governance-floor.yml@",
    "github.event.pull_request.base.sha",
    "github.event.pull_request.head.sha",
)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _commit(root: Path, ref: str, label: str) -> str:
    result = _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    value = result.stdout.strip()
    if result.returncode or FULL_SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{label} ref is not an exact commit")
    return value


def _read_at(root: Path, ref: str, path: str) -> str | None:
    result = _git(root, "show", f"{ref}:{path}")
    return result.stdout if result.returncode == 0 else None


def _tree_has_path(root: Path, ref: str, path: str) -> bool:
    result = _git(root, "ls-tree", "-r", "--name-only", ref, "--", path)
    return result.returncode == 0 and bool(result.stdout.strip())


def _profile(text: str | None, label: str) -> dict[str, object]:
    if text is None:
        raise ValueError(f"{label} .engineering/project.yaml is missing")
    try:
        payload = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{label} .engineering/project.yaml is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} .engineering/project.yaml is invalid")
    return payload


def _policy_epoch(profile: dict[str, object], label: str) -> int:
    engineering = profile.get("engineering_system") or {}
    value = engineering.get("policy_epoch", 0) if isinstance(engineering, dict) else 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} engineering_system.policy_epoch is invalid")
    return value


def evaluate(root: Path, base_ref: str, head_ref: str) -> tuple[str, list[str], int, int]:
    root = root.resolve()
    base = _commit(root, base_ref, "base")
    head = _commit(root, head_ref, "head")
    base_profile = _profile(_read_at(root, base, ".engineering/project.yaml"), "base")
    head_profile = _profile(_read_at(root, head, ".engineering/project.yaml"), "head")
    base_epoch = _policy_epoch(base_profile, "base")
    head_epoch = _policy_epoch(head_profile, "head")
    reasons: list[str] = []

    if head_epoch < base_epoch:
        reasons.append(
            f"GOVERNANCE_POLICY_EPOCH_REGRESSION:base={base_epoch}:head={head_epoch}"
        )

    head_helper = _read_at(root, head, GOVERNANCE_HELPER)
    base_helper = _read_at(root, base, GOVERNANCE_HELPER)
    if head_helper is None:
        reasons.append(f"MANAGED_GOVERNANCE_PATH_MISSING:{GOVERNANCE_HELPER}")
    elif head_epoch == base_epoch and base_helper is not None and head_helper != base_helper:
        reasons.append("GOVERNANCE_HELPER_CHANGED_WITHOUT_POLICY_EPOCH")

    workflow = _read_at(root, head, ENGINEERING_WORKFLOW)
    if workflow is None:
        reasons.append(f"MANAGED_GOVERNANCE_PATH_MISSING:{ENGINEERING_WORKFLOW}")
    else:
        for token in REQUIRED_WORKFLOW_TOKENS:
            if token not in workflow:
                reasons.append(f"GOVERNANCE_WORKFLOW_INCOMPLETE:{token}")

    for path in MANAGED_EXECUTION_SURFACES:
        content = _read_at(root, head, path)
        if content is None:
            reasons.append(f"MANAGED_GOVERNANCE_PATH_MISSING:{path}")
            continue
        for retired in RETIRED_EXECUTION_PATTERNS.get(path, ()):
            if retired in content:
                reasons.append(
                    f"RETIRED_IMPLEMENTER_REINTRODUCED:{path}"
                )
                break

    for path in RETIRED_AGENT_ARTIFACTS:
        if _tree_has_path(root, head, path):
            reasons.append(f"RETIRED_AGENT_ARTIFACT_REINTRODUCED:{path}")

    reasons = sorted(set(reasons))
    return ("BLOCK" if reasons else "PASS"), reasons, base_epoch, head_epoch


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--root", default=".")
    check.add_argument("--base-ref", required=True)
    check.add_argument("--head-ref", required=True)
    args = parser.parse_args()

    try:
        status, reasons, base_epoch, head_epoch = evaluate(
            Path(args.root), args.base_ref, args.head_ref
        )
    except ValueError as exc:
        print("GOVERNANCE_FLOOR=BLOCK")
        print(f"REASON={exc}")
        return 2

    print(f"GOVERNANCE_FLOOR_BASE_EPOCH={base_epoch}")
    print(f"GOVERNANCE_FLOOR_HEAD_EPOCH={head_epoch}")
    for reason in reasons:
        print(f"REASON={reason}")
    print(f"GOVERNANCE_FLOOR={status}")
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
