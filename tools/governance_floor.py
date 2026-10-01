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
)
PROTECTED_GOVERNANCE_SURFACES = (
    # These helpers carry executable policy semantics and may change only with
    # an explicit policy-epoch advance. The governance-floor helper itself is
    # a base-owned root of trust and is handled separately as immutable.
    "tools/context_epoch.py",
    "tools/engineering-context.py",
)
RETIRED_AGENT_ARTIFACTS = (".cursor", ".cursorignore", ".cursorrules")
GOVERNANCE_HELPER = "tools/governance_floor.py"
ENGINEERING_WORKFLOW = ".github/workflows/engineering-system.yml"
GOVERNANCE_WORKFLOW_PREFIX = (
    "datarelay-labs/engineering-system/.github/workflows/governance-floor.yml@"
)
EXPECTED_WORKFLOW_CONDITION = "github.event_name == 'pull_request_target'"
EXPECTED_BASE_INPUT = "${{ github.event.pull_request.base.sha }}"
EXPECTED_HEAD_INPUT = "${{ github.event.pull_request.head.sha }}"
RETIRED_AGENTS_RE = re.compile(
    r"(?i)(?:"
    r"IMPLEMENTER\s*=\s*CURSOR|"
    r"cursor[-_ ]?agent|"
    r"\bagent\s+persist\b|"
    r"/work-resume\b|"
    r"\.cursor(?:/|\b)|"
    r"\bcursor\s+(?:adapter|session|implementation|implementer|worker)\b|"
    r"\b(?:start|resume|launch|wait\s+for|hand\s+off\s+to)\s+(?:the\s+)?cursor\b"
    r")"
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


def _workflow_reasons(text: str | None, profile: dict[str, object]) -> list[str]:
    if text is None:
        return [f"MANAGED_GOVERNANCE_PATH_MISSING:{ENGINEERING_WORKFLOW}"]
    try:
        payload = yaml.load(text, Loader=yaml.BaseLoader) or {}
    except yaml.YAMLError:
        return ["GOVERNANCE_WORKFLOW_INVALID_YAML"]
    if not isinstance(payload, dict):
        return ["GOVERNANCE_WORKFLOW_INVALID_ROOT"]
    triggers = payload.get("on")
    if not isinstance(triggers, dict) or "pull_request_target" not in triggers:
        return ["GOVERNANCE_WORKFLOW_TRIGGER_MISSING"]
    if triggers.get("pull_request_target") not in (None, ""):
        return ["GOVERNANCE_WORKFLOW_TRIGGER_FILTERED"]
    jobs = payload.get("jobs")
    if not isinstance(jobs, dict):
        return ["GOVERNANCE_WORKFLOW_JOB_MISSING"]
    job = jobs.get("governance-floor")
    if not isinstance(job, dict):
        return ["GOVERNANCE_WORKFLOW_JOB_MISSING"]
    reasons: list[str] = []
    if str(job.get("if") or "").strip() != EXPECTED_WORKFLOW_CONDITION:
        reasons.append("GOVERNANCE_WORKFLOW_CONDITION_INVALID")
    uses = str(job.get("uses") or "").strip()
    if not uses.startswith(GOVERNANCE_WORKFLOW_PREFIX):
        reasons.append("GOVERNANCE_WORKFLOW_USES_INVALID")
    else:
        pinned = uses.removeprefix(GOVERNANCE_WORKFLOW_PREFIX)
        if FULL_SHA_RE.fullmatch(pinned) is None:
            reasons.append("GOVERNANCE_WORKFLOW_PIN_INVALID")
        engineering = profile.get("engineering_system") or {}
        baseline = (
            str(engineering.get("baseline") or "").strip()
            if isinstance(engineering, dict)
            else ""
        )
        if baseline and FULL_SHA_RE.fullmatch(baseline) and pinned != baseline:
            reasons.append("GOVERNANCE_WORKFLOW_BASELINE_MISMATCH")
    inputs = job.get("with")
    if not isinstance(inputs, dict):
        reasons.append("GOVERNANCE_WORKFLOW_INPUTS_INVALID")
    else:
        if str(inputs.get("base_sha") or "").strip() != EXPECTED_BASE_INPUT:
            reasons.append("GOVERNANCE_WORKFLOW_BASE_INPUT_INVALID")
        if str(inputs.get("head_sha") or "").strip() != EXPECTED_HEAD_INPUT:
            reasons.append("GOVERNANCE_WORKFLOW_HEAD_INPUT_INVALID")
    return reasons


def _execution_surface_reasons(path: str, content: str) -> list[str]:
    reasons: list[str] = []
    if path == "AGENTS.md":
        if RETIRED_AGENTS_RE.search(content):
            reasons.append("RETIRED_IMPLEMENTER_REINTRODUCED:AGENTS.md")
        for required in (
            "ChatGPT Chat is the implementation path.",
            "Execution authority precedence:",
            "IMPLEMENTER=CHATGPT_CHAT",
        ):
            if required not in content:
                reasons.append(f"MANAGED_EXECUTION_INVARIANT_MISSING:AGENTS.md:{required}")
    elif path == "tools/context_epoch.py":
        if '"CURSOR"' in content or "'CURSOR'" in content:
            reasons.append("RETIRED_IMPLEMENTER_REINTRODUCED:tools/context_epoch.py")
        required = (
            'if implementer and implementer != "CHATGPT_CHAT":',
            'blocking.append("IMPLEMENTER_INVALID")',
        )
        for token in required:
            if token not in content:
                reasons.append(
                    f"MANAGED_EXECUTION_INVARIANT_MISSING:tools/context_epoch.py:{token}"
                )
    elif path == "tools/engineering-context.py":
        if ".cursor" in content.lower():
            reasons.append(
                "RETIRED_IMPLEMENTER_REINTRODUCED:tools/engineering-context.py"
            )
        for required in ("AGENTS.md", ".engineering/project.yaml"):
            if required not in content:
                reasons.append(
                    f"MANAGED_EXECUTION_INVARIANT_MISSING:tools/engineering-context.py:{required}"
                )
    return reasons



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

    base_helper = _read_at(root, base, GOVERNANCE_HELPER)
    head_helper = _read_at(root, head, GOVERNANCE_HELPER)
    if base_helper is not None and head_helper != base_helper:
        reasons.append("GOVERNANCE_ROOT_OF_TRUST_CHANGED")

    if head_epoch == base_epoch:
        for path in PROTECTED_GOVERNANCE_SURFACES:
            base_content = _read_at(root, base, path)
            head_content = _read_at(root, head, path)
            if base_content != head_content:
                reasons.append(
                    f"GOVERNANCE_SURFACE_CHANGED_WITHOUT_POLICY_EPOCH:{path}"
                )

    workflow = _read_at(root, head, ENGINEERING_WORKFLOW)
    reasons.extend(_workflow_reasons(workflow, head_profile))

    for path in MANAGED_EXECUTION_SURFACES:
        content = _read_at(root, head, path)
        if content is None:
            reasons.append(f"MANAGED_GOVERNANCE_PATH_MISSING:{path}")
            continue
        reasons.extend(_execution_surface_reasons(path, content))

    if head_helper is None:
        reasons.append(f"MANAGED_GOVERNANCE_PATH_MISSING:{GOVERNANCE_HELPER}")
    else:
        for required in (
            "GOVERNANCE_POLICY_EPOCH_REGRESSION",
            "GOVERNANCE_SURFACE_CHANGED_WITHOUT_POLICY_EPOCH",
            "RETIRED_IMPLEMENTER_REINTRODUCED",
            "GOVERNANCE_WORKFLOW_TRIGGER_MISSING",
        ):
            if required not in head_helper:
                reasons.append(
                    f"GOVERNANCE_HELPER_INVARIANT_MISSING:{required}"
                )

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
