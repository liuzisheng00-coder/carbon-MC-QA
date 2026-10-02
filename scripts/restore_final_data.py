"""Download, verify and restore the final study data from GitHub Releases.

Usage: python scripts/restore_final_data.py [--download] [--destination PATH]
       python scripts/restore_final_data.py [--destination PATH] --verify-only
Only the Python standard library is needed. Existing files with different bytes
are never overwritten; matching files are left untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "final-data-manifest.json"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def target_path(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
        raise ValueError(f"Unsafe data path: {name}")
    target = (root / Path(*relative.parts)).resolve()
    if not target.is_relative_to(root) or target == root:
        raise ValueError(f"Data path escapes the destination: {name}")
    return target


def matches(path: Path, record: dict) -> bool:
    return (path.is_file() and path.stat().st_size == record["bytes"]
            and digest(path) == record["sha256"])


def install_verified(temporary: Path, target: Path, record: dict) -> bool:
    # Both files are in the same directory. A hard link publishes the complete
    # verified file atomically and fails if another writer created the target.
    try:
        os.link(temporary, target)
        return True
    except FileExistsError:
        if not matches(target, record):
            raise ValueError(f"Existing file changed during restore: {target}")
        return False


def download_archive(path: Path, record: dict) -> None:
    url = record["downloadUrl"]
    prefix = "https://github.com/liuzisheng00-coder/carbon-MC-QA/releases/download/"
    if not url.startswith(prefix) or url.rsplit("/", 1)[-1] != path.name:
        raise ValueError(f"Unexpected data download URL for {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    print(f"Downloading {path.name} ({record['bytes']:,} bytes)...", flush=True)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "carbon-MC-QA-data-restore"})
        value = hashlib.sha256()
        size = 0
        with urllib.request.urlopen(request, timeout=120) as source:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".download-", delete=False) as stream:
                temporary = Path(stream.name)
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    size += len(block)
                    if size > record["bytes"]:
                        raise ValueError(f"Download exceeds the recorded size: {path.name}")
                    value.update(block)
                    stream.write(block)
        if size != record["bytes"] or value.hexdigest() != record["sha256"]:
            raise ValueError(f"Downloaded archive checksum mismatch: {path.name}")
        install_verified(temporary, path, record)
        print(f"Downloaded and verified {path.name}.", flush=True)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=ROOT)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--verify-only", action="store_true", help="Verify restored files without writing anything")
    operation.add_argument("--download", action="store_true", help="Download missing archives from GitHub Releases before restoring")
    args = parser.parse_args()
    destination = args.destination.resolve()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != 1:
        raise ValueError("Unsupported data manifest version")
    records = {row["path"]: row for row in manifest["files"]}
    if len(records) != len(manifest["files"]):
        raise ValueError("Duplicate file paths in the manifest")
    targets = {name: target_path(destination, name) for name in records}

    if args.verify_only:
        failed = [name for name, record in records.items() if not matches(targets[name], record)]
        if failed:
            print(f"Missing or changed data files: {len(failed)}", file=sys.stderr)
            for name in failed[:20]:
                print(name, file=sys.stderr)
            return 1
        print(f"Verified {len(records)} restored files against SHA-256 and byte sizes.")
        return 0

    # Preflight everything before writing: manifest membership, paths, archives,
    # file checksums and any conflicts with user-owned existing files.
    conflicts = [name for name, record in records.items()
                 if targets[name].exists() and not matches(targets[name], record)]
    if conflicts:
        raise ValueError("Existing files differ; use a fresh destination or preserve them before restoring: "
                         + ", ".join(conflicts[:10]))
    archive_paths = {}
    covered = set()
    for item in manifest["archives"]:
        path = target_path(ROOT, item["path"])
        if not path.exists():
            if args.download:
                download_archive(path, item)
            else:
                raise ValueError(f"Missing archive: {item['path']}; run again with --download")
        if path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
            raise ValueError(f"Archive checksum mismatch: {item['path']}")
        archive_paths[item["path"]] = path
        expected = {name for name, record in records.items() if record["archive"] == item["path"]}
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) != expected:
                raise ValueError(f"Archive membership mismatch: {item['path']}")
            for info in archive.infolist():
                record = records[info.filename]
                if info.file_size != record["bytes"]:
                    raise ValueError(f"Archive member size mismatch: {info.filename}")
                value = hashlib.sha256()
                with archive.open(info) as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        value.update(block)
                if value.hexdigest() != record["sha256"]:
                    raise ValueError(f"Archive member checksum mismatch: {info.filename}")
        covered.update(expected)
    if covered != set(records):
        raise ValueError("Manifest refers to files outside the listed archives")

    count = 0
    for archive_name, path in archive_paths.items():
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                target = targets[name]
                if matches(target, records[name]):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                # Write each verified file atomically without exposing partial data.
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".restore-", delete=False) as stream:
                        temporary = Path(stream.name)
                        with archive.open(name) as source:
                            shutil.copyfileobj(source, stream)
                    count += int(install_verified(temporary, target, records[name]))
                finally:
                    if temporary is not None and temporary.exists():
                        temporary.unlink()
    print(f"Restored {count} files to {destination}; {len(records) - count} matching files already present.")
    print(f"All {len(records)} files were checked against SHA-256 and byte sizes.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f"Data restore failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
