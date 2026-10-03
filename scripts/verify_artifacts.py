#!/usr/bin/env python3
"""Verify archived data integrity offline; never execute stored code."""

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def verify(root: Path, manifest_path: Path, verbose: bool = False) -> dict:
    root = root.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors, seen = [], set()
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("Manifest must contain a nonempty artifacts list")
    for entry in artifacts:
        relative = entry["path"]
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            errors.append(f"Path escapes repository: {relative}")
            continue
        if relative in seen:
            errors.append(f"Duplicate manifest path: {relative}")
        seen.add(relative)
        try:
            data = path.read_bytes()
        except OSError as exc:
            errors.append(f"{relative}: {exc}")
            continue
        if len(data) != entry["bytes"]:
            errors.append(f"Byte count differs: {relative}")
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            errors.append(f"SHA256 differs: {relative}")
        if verbose:
            print(f"Checked {relative}")

    # Include metadata tables that are intentionally outside the raw-data manifest.
    json_files, jsonl_records = 0, 0
    for path in sorted((root / "results").rglob("*")):
        if not path.is_file() or path.suffix not in {".json", ".jsonl"}:
            continue
        relative = path.relative_to(root).as_posix()
        try:
            content = path.read_text(encoding="utf-8")
            if path.suffix == ".json":
                json.loads(content)
            else:
                for line_no, line in enumerate(content.splitlines(), 1):
                    if not line.strip():
                        continue
                    try:
                        json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"line {line_no}: {exc}") from exc
                    jsonl_records += 1
            json_files += 1
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"Invalid JSON data in {relative}: {exc}")
    return {"artifacts": len(artifacts), "json_files": json_files,
            "jsonl_records": jsonl_records, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    parser.add_argument("--manifest", type=Path, help="manifest path relative to root, or absolute")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    manifest = args.root / (args.manifest or Path("results/manifest.json"))
    try:
        result = verify(args.root, manifest, args.verbose)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Verification failed: {exc}\n")
    if result["errors"]:
        parser.exit(1, "Verification failed:\n" + "\n".join(result["errors"]) + "\n")
    print(f"OK: {result['artifacts']} archived artifacts match SHA256 and byte counts; "
          f"{result['json_files']} JSON/JSONL files parse successfully "
          f"({result['jsonl_records']} JSONL records).")
    print("Offline integrity check only; no benchmark code or API requests were executed.")


if __name__ == "__main__":
    main()
