#!/usr/bin/env python3
"""Structural spec/trace drift check; never attests semantic or runtime correctness."""
import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON key: " + key)
        obj[key] = value
    return obj


def inspect(root, manifest, complete=False):
    errors, gaps = [], []
    docs, scopes, ids = {}, {}, set()
    root = root.resolve()

    def read(path):
        if not isinstance(path, str) or not path or "\\" in path:
            raise ValueError("invalid path")
        rel = PurePosixPath(path)
        if rel.is_absolute() or ".." in rel.parts or rel.as_posix() != path:
            raise ValueError("noncanonical path: " + path)
        candidate = root / path
        if any((root / Path(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts)+1)):
            raise ValueError("symlink path: " + path)
        if not candidate.is_file():
            raise ValueError("missing file: " + path)
        return candidate.read_bytes()

    def reference(ref):
        path, anchor = ref["path"], ref["anchor"]
        if not isinstance(anchor, str) or not anchor.strip():
            raise ValueError("empty anchor: " + str(path))
        if read(path).decode("utf-8").count(anchor) != 1:
            raise ValueError("missing or ambiguous anchor: " + path)
        return path

    if type(manifest.get("version")) is not int or manifest["version"] != 1:
        raise ValueError("unsupported manifest version")
    for entry in manifest["documents"]:
        path, state = entry["path"], entry["state"]
        if path in docs:
            errors.append("DUPLICATE_DOCUMENT:" + path)
        docs[path] = state
        try:
            actual = hashlib.sha256(read(path)).hexdigest()
            if actual != entry["sha256"]:
                errors.append("DOCUMENT_CHANGED_REVIEW_REQUIRED:" + path)
            if state == "current":
                scope = entry["scope"]
                if not isinstance(scope, str) or not scope.strip():
                    raise ValueError("empty authority scope")
                if scope in scopes:
                    errors.append("DUPLICATE_AUTHORITY:" + scope)
                scopes[scope] = path
            elif state in ("historical", "supporting"):
                if not entry.get("reason", "").strip():
                    raise ValueError("non-authority classification needs reason")
            elif state == "mixed":
                gaps.append("MIXED_AUTHORITY:" + path)
            else:
                raise ValueError("unknown document state")
        except (ValueError, KeyError, OSError, TypeError, AttributeError) as exc:
            errors.append("DOCUMENT_INVALID:" + str(path) + ":" + str(exc))
    patterns = manifest["inventory_globs"]
    if not isinstance(patterns, list) or not patterns:
        raise ValueError("inventory_globs must be nonempty")
    for pattern in patterns:
        if not isinstance(pattern, str) or PurePosixPath(pattern).is_absolute() or ".." in PurePosixPath(pattern).parts:
            raise ValueError("invalid inventory glob")
        for path in sorted(root.glob(pattern)):
            if path.is_file() and path.relative_to(root).as_posix() not in docs:
                errors.append("UNCLASSIFIED_DOCUMENT:" + path.relative_to(root).as_posix())

    for req in manifest["requirements"]:
        rid = req["id"]
        if not isinstance(rid, str) or not re.fullmatch(r"[A-Z][A-Z0-9_-]*-[0-9]+", rid):
            raise ValueError("invalid requirement id")
        if rid in ids:
            errors.append("DUPLICATE_REQUIREMENT:" + rid)
        ids.add(rid)
        try:
            source = reference(req["source"])
            if docs.get(source) != "current":
                errors.append("NONCURRENT_REQUIREMENT_SOURCE:" + rid)
            if not req["acceptance"].strip():
                raise ValueError("empty acceptance")
            state = req["state"]
            if state not in ("planned", "linked"):
                raise ValueError("invalid requirement state")
            for field in ("implementation", "tests"):
                refs = req[field]
                if not isinstance(refs, list):
                    raise ValueError("invalid reference list: " + field)
                if not refs:
                    gaps.append("MISSING_" + field.upper() + ":" + rid)
                for ref in refs:
                    path = reference(ref)
                    if field == "tests" and path == source:
                        raise ValueError("spec is not test evidence")
                if len({(r["path"], r["anchor"]) for r in refs}) != len(refs):
                    raise ValueError("duplicate reference: " + field)
            if state == "planned":
                gaps.append("PLANNED_REQUIREMENT:" + rid)
        except (ValueError, KeyError, OSError, TypeError, AttributeError) as exc:
            errors.append("REQUIREMENT_INVALID:" + rid + ":" + str(exc))
    for finding in manifest.get("open_findings", []):
        if not isinstance(finding, str) or not finding.strip():
            raise ValueError("invalid open finding")
        gaps.append("OPEN_FINDING:" + finding)
    if not docs or not ids:
        errors.append("EMPTY_DECLARED_SCOPE")
    return {
        "authority": "STRUCTURAL_ONLY",
        "declared_documents": len(docs),
        "declared_requirements": len(ids),
        "errors": sorted(set(errors)),
        "gaps": sorted(set(gaps)),
        "semantic_coverage": "NOT_ESTABLISHED",
        "test_execution": "NOT_ESTABLISHED",
        "status": "BLOCK" if errors or (complete and gaps) else "INCOMPLETE" if gaps else "STRUCTURAL_PASS",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(), object_pairs_hook=unique_object)
        result = inspect(args.root, manifest, args.require_complete)
    except (ValueError, KeyError, OSError, TypeError, AttributeError) as exc:
        result = {"authority": "STRUCTURAL_ONLY", "status": "BLOCK", "errors": [str(exc)]}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result["status"] == "BLOCK" else 0


if __name__ == "__main__":
    raise SystemExit(main())
