#!/usr/bin/env python3
"""Bounded specification traceability check; never a semantic or release verdict."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


class Invalid(ValueError):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Invalid(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def shape(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise Invalid(f"{label}: expected keys {', '.join(keys)}")


def read(root, name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise Invalid(f"invalid repository path: {name!r}")
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or str(p) != name or name == ".":
        raise Invalid(f"invalid repository path: {name!r}")
    current = root
    for part in p.parts:
        current = current / part
        if current.is_symlink():
            raise Invalid(f"symlink is not permitted: {name}")
    if not current.is_file():
        raise Invalid(f"missing file: {name}")
    try:
        return current.read_bytes()
    except OSError as exc:
        raise Invalid(f"cannot read {name}: {exc}") from exc


def digest(data):
    return hashlib.sha256(data).hexdigest()


def marker(root, reference, label):
    shape(reference, ("path", "contains"), label)
    needle = reference["contains"]
    if not isinstance(needle, str) or len(needle.strip()) < 12:
        raise Invalid(f"{label}: missing substantive marker")
    data = read(root, reference["path"])
    if needle.encode("utf-8") not in data:
        raise Invalid(f"{label}: marker missing in {reference['path']}")
    return reference["path"]


REQUIREMENT_TAG = re.compile(rb"<!-- requirement: ([A-Z][A-Z0-9]*-[0-9]+) -->")


def scoped_inventory(root, patterns, documents):
    if not isinstance(patterns, list) or not patterns:
        raise Invalid("empty specification scope globs")
    matched = set()
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern or "\\" in pattern or ":" in pattern:
            raise Invalid("invalid specification scope glob")
        path = PurePosixPath(pattern)
        if path.is_absolute() or ".." in path.parts or str(path) != pattern or pattern == ".":
            raise Invalid("invalid specification scope glob")
        paths = {p.relative_to(root).as_posix() for p in root.glob(pattern) if p.is_file()}
        if not paths:
            raise Invalid(f"specification scope glob matched no files: {pattern}")
        matched.update(paths)
    missing = matched - set(documents)
    extra = set(documents) - matched
    if missing:
        raise Invalid("unclassified specification files: " + ", ".join(sorted(missing)))
    if extra:
        raise Invalid("declared specification outside scope: " + ", ".join(sorted(extra)))


def declared_tags(root, active_docs):
    tagged = {}
    for path in sorted(active_docs):
        data = read(root, path)
        matches = list(REQUIREMENT_TAG.finditer(data))
        if data.count(b"<!-- requirement:") != len(matches):
            raise Invalid(f"{path}: malformed requirement tag")
        for index, match in enumerate(matches):
            ident = match.group(1).decode("ascii")
            if ident in tagged:
                raise Invalid(f"duplicate declared requirement tag: {ident}")
            end = matches[index + 1].start() if index + 1 < len(matches) else len(data)
            tagged[ident] = (path, data[match.end():end])
    return tagged


def validate(root, manifest, require_implemented=False):
    if not isinstance(manifest, dict) or type(manifest.get("version")) is not int or manifest["version"] not in (1, 2):
        raise Invalid("unsupported manifest version")
    version = manifest["version"]
    keys = ("version", "documents", "requirements")
    shape(manifest, keys + (("inventory_globs",) if version == 2 else ()), "manifest")
    documents = manifest["documents"]
    requirements = manifest["requirements"]
    if not isinstance(documents, dict) or not documents:
        raise Invalid("empty or invalid document inventory")
    if not isinstance(requirements, list) or not requirements:
        raise Invalid("empty or invalid requirement inventory")
    if version == 2:
        scoped_inventory(root, manifest["inventory_globs"], documents)
    active_docs = set()
    for path, doc in documents.items():
        shape(doc, ("status", "sha256"), path)
        statuses = ("active", "historical", "mixed") + (("supporting",) if version == 2 else ())
        if doc["status"] not in statuses:
            raise Invalid(f"{path}: invalid document status")
        if doc["status"] == "mixed":
            raise Invalid(f"{path}: unresolved active/historical specification")
        if not isinstance(doc["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", doc["sha256"]):
            raise Invalid(f"{path}: invalid SHA-256")
        if digest(read(root, path)) != doc["sha256"]:
            raise Invalid(f"{path}: specification changed; reconcile and review before refreshing digest")
        if doc["status"] == "active":
            active_docs.add(path)
    if not active_docs:
        raise Invalid("no active specification")
    tags = declared_tags(root, active_docs) if version == 2 else {}
    live_ids = set()
    ids, anchors, covered = set(), set(), set()
    counts = {"planned": 0, "implemented": 0, "retired": 0}
    for req in requirements:
        shape(req, ("id", "state", "source", "implementation", "tests"), "requirement")
        ident, state = req["id"], req["state"]
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Z][A-Z0-9]*-[0-9]+", ident):
            raise Invalid(f"invalid requirement ID: {ident!r}")
        if ident in ids:
            raise Invalid(f"duplicate requirement ID: {ident}")
        ids.add(ident)
        if not isinstance(state, str) or state not in counts:
            raise Invalid(f"{ident}: invalid state")
        counts[state] += 1
        path = marker(root, req["source"], ident + " source")
        if path not in documents:
            raise Invalid(f"{ident}: undeclared specification authority")
        anchor = (path, req["source"]["contains"])
        if anchor in anchors:
            raise Invalid(f"{ident}: duplicate authority marker")
        anchors.add(anchor)
        if state != "retired":
            if path not in active_docs:
                raise Invalid(f"{ident}: historical source cannot authorize active behavior")
            covered.add(path)
            live_ids.add(ident)
            if version == 2:
                needle = req["source"]["contains"].encode("utf-8")
                if ident not in tags or tags[ident][0] != path or tags[ident][1].count(needle) != 1:
                    raise Invalid(f"{ident}: source marker is not uniquely bound to its declared requirement tag")
                if read(root, path).count(needle) != 1:
                    raise Invalid(f"{ident}: ambiguous source marker")
        for kind in ("implementation", "tests"):
            refs = req[kind]
            if not isinstance(refs, list):
                raise Invalid(f"{ident}: invalid {kind} list")
            if state == "implemented" and not refs:
                raise Invalid(f"{ident}: missing {kind} trace")
            if state == "retired" and refs:
                raise Invalid(f"{ident}: retired requirement has live traces")
            seen = set()
            for ref in refs:
                target = marker(root, ref, ident + " " + kind)
                if target in documents:
                    raise Invalid(f"{ident}: specification cannot substitute for {kind}")
                pair = (target, ref["contains"])
                if pair in seen:
                    raise Invalid(f"{ident}: duplicate {kind} trace")
                seen.add(pair)
        if state == "implemented":
            if {ref["path"] for ref in req["implementation"]} & {ref["path"] for ref in req["tests"]}:
                raise Invalid(f"{ident}: implementation cannot substitute for test file")
    if version == 2 and set(tags) != live_ids:
        raise Invalid("declared requirement missing trace: " + ", ".join(sorted(set(tags) - live_ids)))
    if covered != active_docs:
        raise Invalid("active document missing requirement inventory: " + ", ".join(sorted(active_docs - covered)))
    if require_implemented and counts["planned"]:
        raise Invalid(f"planned requirements remain: {counts['planned']}")
    return counts



def baseline_delta(base, candidate):
    """Compare declared inventory only; the reviewed Git base is supplied by the caller."""
    def index(manifest):
        if not isinstance(manifest, dict) or type(manifest.get("version")) is not int or manifest["version"] not in (1, 2):
            raise Invalid("unsupported baseline manifest")
        entries = manifest.get("requirements")
        docs = manifest.get("documents")
        globs = manifest.get("inventory_globs", [])
        if not isinstance(entries, list) or not entries or not isinstance(docs, dict) or not docs:
            raise Invalid("invalid baseline inventory")
        if not isinstance(globs, list) or any(not isinstance(p, str) for p in globs):
            raise Invalid("invalid baseline scope")
        result = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise Invalid("invalid baseline requirement")
            ident = entry.get("id")
            state = entry.get("state")
            if (not isinstance(ident, str) or not re.fullmatch(r"[A-Z][A-Z0-9]*-[0-9]+", ident)
                    or ident in result or state not in ("planned", "implemented", "retired")):
                raise Invalid("invalid baseline requirement identity or state")
            result[ident] = entry
        if any(not isinstance(doc, dict) or doc.get("status") not in ("active", "historical", "supporting", "mixed")
               for doc in docs.values()):
            raise Invalid("invalid baseline document status")
        return result, docs, set(globs)

    old, old_docs, old_globs = index(base)
    new, new_docs, new_globs = index(candidate)
    missing = sorted(ident for ident, entry in old.items()
                     if entry["state"] != "retired" and ident not in new)
    if missing:
        raise Invalid("previously live requirement missing: " + ", ".join(missing))
    shared = sorted(old.keys() & new.keys())
    return {
        "added_requirements": sorted(new.keys() - old.keys()),
        "state_changes": {i: [old[i]["state"], new[i]["state"]] for i in shared
                          if old[i]["state"] != new[i]["state"]},
        "changed_authorities": [i for i in shared if old[i].get("source") != new[i].get("source")],
        "changed_traces": [i for i in shared if any(old[i].get(k) != new[i].get(k)
                           for k in ("implementation", "tests"))],
        "removed_documents": sorted(old_docs.keys() - new_docs.keys()),
        "added_documents": sorted(new_docs.keys() - old_docs.keys()),
        "changed_document_status": sorted(p for p in old_docs.keys() & new_docs.keys()
                                          if old_docs[p]["status"] != new_docs[p]["status"]),
        "changed_document_digests": sorted(p for p in old_docs.keys() & new_docs.keys()
                                           if old_docs[p].get("sha256") != new_docs[p].get("sha256")),
        "removed_globs": sorted(old_globs - new_globs),
        "added_globs": sorted(new_globs - old_globs),
    }


def read_base(root, sha, manifest_path):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise Invalid("base must be an exact 40-character Git commit SHA")
    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=15, check=True).stdout
    try:
        if Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve() != root:
            raise Invalid("base comparison requires the Git repository root")
        if git("cat-file", "-t", sha).strip() != b"commit":
            raise Invalid("base is not a Git commit")
        return json.loads(git("show", f"{sha}:{manifest_path}"), object_pairs_hook=unique_object)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise Invalid("base manifest unavailable at the specified Git commit") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", default=".engineering/specification.json")
    parser.add_argument("--require-implemented", action="store_true")
    parser.add_argument("--base", help="Compare with the manifest at a reviewed exact Git commit SHA")
    parser.add_argument("--requirement", help="Locate one declared requirement, implementation and test trace")
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve(strict=True)
        manifest = json.loads(read(root, args.manifest), object_pairs_hook=unique_object)
        counts = validate(root, manifest, args.require_implemented)
        delta = baseline_delta(read_base(root, args.base, args.manifest), manifest) if args.base else None
        trace = None
        if args.requirement:
            trace = next((entry for entry in manifest["requirements"]
                          if entry["id"] == args.requirement), None)
            if trace is None:
                raise Invalid(f"unknown requirement: {args.requirement}")
    except (Invalid, OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        print(f"SPECIFICATION_STRUCTURE=FAIL: {exc}", file=sys.stderr)
        return 1
    print("SPECIFICATION_STRUCTURE=PASS " + json.dumps(counts, sort_keys=True))
    if delta is not None:
        print("BASELINE_DELTA=" + json.dumps(delta, sort_keys=True))
        print("BASELINE_REVIEW=" + ("REQUIRED" if any(delta.values()) else "NO_DECLARED_CHANGE"))
    if trace is not None:
        print("REQUIREMENT_TRACE=" + json.dumps(trace, sort_keys=True))
    print("AUTHORITY=STRUCTURAL_ONLY; SEMANTIC_CONSISTENCY=UNVERIFIED; TEST_EXECUTION=UNVERIFIED; INVENTORY_COMPLETENESS=UNVERIFIED; AUTHORIZES_RELEASE=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
