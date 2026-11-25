#!/usr/bin/env python3
"""Bounded specification traceability check; never a semantic or release verdict."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
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


def validate(root, manifest, require_implemented=False):
    shape(manifest, ("version", "documents", "requirements"), "manifest")
    if type(manifest["version"]) is not int or manifest["version"] != 1:
        raise Invalid("unsupported manifest version")
    documents = manifest["documents"]
    requirements = manifest["requirements"]
    if not isinstance(documents, dict) or not documents:
        raise Invalid("empty or invalid document inventory")
    if not isinstance(requirements, list) or not requirements:
        raise Invalid("empty or invalid requirement inventory")
    active_docs = set()
    for path, doc in documents.items():
        shape(doc, ("status", "sha256"), path)
        if doc["status"] not in ("active", "historical", "mixed"):
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
    if covered != active_docs:
        raise Invalid("active document missing requirement inventory: " + ", ".join(sorted(active_docs - covered)))
    if require_implemented and counts["planned"]:
        raise Invalid(f"planned requirements remain: {counts['planned']}")
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", default=".engineering/specification.json")
    parser.add_argument("--require-implemented", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve(strict=True)
        manifest = json.loads(read(root, args.manifest), object_pairs_hook=unique_object)
        counts = validate(root, manifest, args.require_implemented)
    except (Invalid, OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        print(f"SPECIFICATION_STRUCTURE=FAIL: {exc}", file=sys.stderr)
        return 1
    print("SPECIFICATION_STRUCTURE=PASS " + json.dumps(counts, sort_keys=True))
    print("AUTHORITY=STRUCTURAL_ONLY; SEMANTIC_CONSISTENCY=UNVERIFIED; TEST_EXECUTION=UNVERIFIED; INVENTORY_COMPLETENESS=UNVERIFIED; AUTHORIZES_RELEASE=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
