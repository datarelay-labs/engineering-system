#!/usr/bin/env python3
"""Emit minimal deterministic context for an existing branch or PR."""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
from pathlib import Path

try:
    import yaml  # type: ignore
except ModuleNotFoundError:
    yaml = None


MAX_ORIENTATION_FILES = 40
MAX_TRACKED_FILES = 50000
MAX_TASK_BYTES = 4096
MAX_PATH_BYTES = 2048
HEAD_RE = re.compile(r"^[0-9a-f]{40}$")
TERM_RE = re.compile(r"[\w][\w.-]{1,}", re.UNICODE)
CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
TOKEN_SEPARATOR_RE = re.compile(r"[_/:=.-]+")
MANDATORY_CONTEXT_PATHS = frozenset({"AGENTS.md", ".engineering/project.yaml"})


def fail_context(message: str) -> None:
    raise SystemExit(f"CONTEXT_ROUTER=FAIL {message}")


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(args=["git", *args], returncode=127, stdout="")


def run_git_bytes(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(args=["git", *args], returncode=127, stdout=b"")


def decode_git_path(raw: bytes) -> str:
    return raw.decode("utf-8", errors="surrogateescape")


def nul_records(payload: bytes) -> list[bytes]:
    if not payload:
        return []
    records = payload.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    return records


def parse_status_z(payload: bytes) -> set[str]:
    found: set[str] = set()
    records = nul_records(payload)
    index = 0
    while index < len(records):
        entry = records[index]
        if len(entry) < 4 or entry[2:3] != b" ":
            fail_context("malformed git status output")
        status = entry[:2]
        paths = [decode_git_path(entry[3:])]
        index += 1
        if b"R" in status or b"C" in status:
            if index >= len(records):
                fail_context("malformed git status output")
            paths.append(decode_git_path(records[index]))
            index += 1
        found.update(path for path in paths if path)
    return found


def parse_name_status_z(payload: bytes) -> set[str]:
    found: set[str] = set()
    records = nul_records(payload)
    index = 0
    while index < len(records):
        status = records[index]
        if not status:
            fail_context("malformed git diff output")
        path_count = 2 if status[:1] in (b"R", b"C") else 1
        if index + 1 + path_count > len(records):
            fail_context("malformed git diff output")
        for offset in range(1, 1 + path_count):
            path = decode_git_path(records[index + offset])
            if path:
                found.add(path)
        index += 1 + path_count
    return found


def git(root: Path, *args: str) -> str:
    completed = run_git(root, *args)
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def resolve_base(root: Path, explicit: str) -> str:
    if explicit:
        if not git(root, "rev-parse", "--verify", "--quiet", f"{explicit}^{{commit}}"):
            fail_context(f"unresolved base: {explicit}")
        return explicit
    symbolic = git(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    candidates = [symbolic, "origin/main", "origin/master", "main", "master", "HEAD^"]
    for candidate in candidates:
        if candidate and git(root, "rev-parse", "--verify", "--quiet", candidate):
            return candidate
    return ""


def committed_paths(root: Path, base: str) -> set[str]:
    completed = run_git_bytes(root, "diff", "-z", "--name-status", "--find-renames", f"{base}...HEAD")
    if completed.returncode != 0:
        fail_context(f"git diff failed for base: {base}")
    return parse_name_status_z(completed.stdout or b"")


def worktree_paths(root: Path) -> set[str]:
    completed = run_git_bytes(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if completed.returncode != 0:
        fail_context("git status failed")
    return parse_status_z(completed.stdout or b"")


def changed_files(root: Path, base: str) -> list[str]:
    found: set[str] = set()
    if base:
        found.update(committed_paths(root, base))
    found.update(worktree_paths(root))
    return sorted(found)


def lexical_terms(value: str) -> frozenset[str]:
    normalized = CAMEL_BOUNDARY_RE.sub(" ", value)
    normalized = TOKEN_SEPARATOR_RE.sub(" ", normalized)
    return frozenset(match.group(0).casefold() for match in TERM_RE.finditer(normalized))


def tracked_files(root: Path) -> list[str]:
    completed = run_git_bytes(root, "ls-files", "-z")
    if completed.returncode != 0:
        fail_context("git ls-files failed")
    records = nul_records(completed.stdout or b"")
    if len(records) > MAX_TRACKED_FILES:
        fail_context("tracked file inventory exceeds orientation bound")
    paths: list[str] = []
    for raw in records:
        path = decode_git_path(raw)
        if not path or path.startswith(("/", "\\")) or "\\" in path:
            continue
        if len(raw) > MAX_PATH_BYTES or any(part in ("", ".", "..") for part in Path(path).parts):
            continue
        candidate = root / path
        if candidate.is_symlink() or not candidate.is_file():
            continue
        paths.append(path)
    return sorted(set(paths))


def knowledge_scores(root: Path, task_terms: frozenset[str]) -> dict[str, int]:
    index = root / ".engineering/knowledge.yaml"
    if not index.is_file() or yaml is None:
        return {}
    try:
        data = yaml.safe_load(index.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        fail_context("invalid .engineering/knowledge.yaml")
    if not isinstance(data, dict):
        fail_context("invalid .engineering/knowledge.yaml")
    scores: dict[str, int] = {}
    for domain in data.get("domains") or []:
        if not isinstance(domain, dict):
            continue
        descriptor = f"{domain.get('id', '')} {domain.get('summary', '')}"
        overlap = len(task_terms & lexical_terms(descriptor))
        if overlap <= 0:
            continue
        for raw in domain.get("canonical") or []:
            if isinstance(raw, str) and raw:
                scores[raw] = max(scores.get(raw, 0), overlap)
    return scores


def orientation(root: Path, task: str, max_files: int) -> dict[str, object]:
    if not task.strip() or len(task.encode("utf-8")) > MAX_TASK_BYTES:
        fail_context("orientation task is empty or exceeds bound")
    if max_files < 1 or max_files > MAX_ORIENTATION_FILES:
        fail_context("max orientation files is outside bound")
    if worktree_paths(root):
        fail_context("orientation requires a clean worktree")
    head = git(root, "rev-parse", "HEAD")
    if HEAD_RE.fullmatch(head) is None:
        fail_context("orientation requires an exact HEAD")
    task_terms = lexical_terms(task)
    if not task_terms:
        fail_context("orientation task has no usable terms")
    domain_scores = knowledge_scores(root, task_terms)
    candidates: list[tuple[int, int, str, str]] = []
    for path in tracked_files(root):
        if path in MANDATORY_CONTEXT_PATHS:
            continue
        path_overlap = len(task_terms & lexical_terms(path))
        domain_overlap = domain_scores.get(path, 0)
        if path_overlap == 0 and domain_overlap == 0:
            continue
        score = (domain_overlap * 100) + (path_overlap * 10)
        reason = "domain_canonical+task_path" if domain_overlap and path_overlap else "domain_canonical" if domain_overlap else "task_path"
        candidates.append((-score, path.count("/"), path, reason))
    candidates.sort()
    selected = candidates[:max_files]
    final_head = git(root, "rev-parse", "HEAD")
    if final_head != head or worktree_paths(root):
        fail_context("orientation repository state changed during scan")
    return {
        "head": head,
        "decision": "READY" if selected else "NO_MATCH",
        "candidates": [
            {"path": path, "reason": reason, "score": -negative_score}
            for negative_score, _depth, path, reason in selected
        ],
    }


def matches(pattern: str, path: str) -> bool:
    if fnmatch.fnmatch(path, pattern):
        return True
    if pattern.endswith("/**") and path.startswith(pattern[:-3].rstrip("/") + "/"):
        return True
    return False


def _fallback_test_path_domains(text: str) -> list[tuple[str, tuple[str, ...]]]:
    """Parse only the managed tests.yaml path->inline-domains subset."""
    entries: list[tuple[str, tuple[str, ...]]] = []
    in_paths = False
    current_pattern: str | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw == "paths:":
            in_paths = True
            current_pattern = None
            continue
        if in_paths and not raw.startswith(" "):
            break
        if not in_paths:
            continue
        pattern_match = re.fullmatch(r'  (?:"([^"]+)"|([^:]+)):', raw)
        if pattern_match:
            current_pattern = pattern_match.group(1) or pattern_match.group(2)
            continue
        domain_match = re.fullmatch(r"    domains:\s*\[([^\]]*)\]\s*", raw)
        if domain_match and current_pattern:
            domains = tuple(
                item.strip().strip('"').strip("'")
                for item in domain_match.group(1).split(",")
                if item.strip()
            )
            entries.append((current_pattern, domains))
            current_pattern = None
    return entries


def affected_domains(root: Path, files: list[str]) -> list[str]:
    manifest = root / ".engineering/tests.yaml"
    if not manifest.is_file():
        return []
    text = manifest.read_text(encoding="utf-8")
    domains: set[str] = set()
    if yaml is not None:
        data = yaml.safe_load(text) or {}
        entries = [
            (str(pattern), tuple(str(item) for item in (spec or {}).get("domains") or []))
            for pattern, spec in (data.get("paths") or {}).items()
        ]
    else:
        entries = _fallback_test_path_domains(text)
    for pattern, mapped_domains in entries:
        if any(matches(pattern, path) for path in files):
            domains.update(mapped_domains)
    return sorted(domains)


def main() -> int:
    parser = argparse.ArgumentParser(description="Show minimum context for the current diff")
    parser.add_argument("--root", default=".")
    parser.add_argument("--base", default="")
    parser.add_argument("--max-files", type=int, default=40)
    parser.add_argument("--task", default="")
    parser.add_argument("--max-orientation", type=int, default=12)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    base = resolve_base(root, args.base)
    files = changed_files(root, base)
    domains = affected_domains(root, files)
    orientation_report = orientation(root, args.task, args.max_orientation) if args.task else None

    print(f"CONTEXT_BASE={base or '<none>'}")
    print(f"CHANGED_COUNT={len(files)}")
    for path in files[: max(args.max_files, 0)]:
        print(f"CHANGED_FILE={path}")
    if len(files) > max(args.max_files, 0):
        print(f"CHANGED_FILES_TRUNCATED={len(files) - max(args.max_files, 0)}")
    print("AFFECTED_DOMAINS=" + (",".join(domains) if domains else "<none>"))
    print("READ=AGENTS.md")
    print("READ=.engineering/project.yaml")
    if files and (root / ".engineering/tests.yaml").is_file():
        print("READ=.engineering/tests.yaml")
    if orientation_report is not None:
        print(f"ORIENTATION_HEAD={orientation_report['head']}")
        print(f"ORIENTATION_DECISION={orientation_report['decision']}")
        candidates = orientation_report["candidates"]
        print(f"ORIENTATION_COUNT={len(candidates)}")
        for item in candidates:
            encoded_path = json.dumps(item["path"], ensure_ascii=True)
            print(f"ORIENTATION_FILE_JSON={encoded_path} REASON={item['reason']} SCORE={item['score']}")
    print("CONTEXT_ROUTER=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
