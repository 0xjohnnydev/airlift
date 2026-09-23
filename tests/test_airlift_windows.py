from __future__ import annotations

import io
import plistlib
import stat
import unittest
import zipfile

import airlift_windows


class WindowsAirLiftTests(unittest.TestCase):
    def test_ios_27_final_is_recorded_as_device_validated(self) -> None:
        self.assertIn(("27.0", "24A437"), airlift_windows.TESTED_BUILDS)

    def test_target_guard_matches_upstream_normalization(self) -> None:
        self.assertEqual(
            airlift_windows.normalize_target("/var/mobile/Library/Caches"),
            "/var/mobile/Library/Caches",
        )
        self.assertEqual(
            airlift_windows.normalize_target("/var/mobile/../root"),
            "/var/root",
        )
        for value in ("relative", "/", "/var/mobile/\x00bad"):
            with self.subTest(value=value), self.assertRaises(airlift_windows.AirLiftError):
                airlift_windows.normalize_target(value)

    def test_archive_preserves_upstream_symlink_shape(self) -> None:
        archive = zipfile.ZipFile(
            io.BytesIO(
                airlift_windows.build_archive(
                    "/var/mobile/Library/SpringBoard",
                    b"canary",
                )
            )
        )
        link = archive.getinfo("p0/p1/p2/link")

        self.assertEqual(stat.S_IFMT(link.external_attr >> 16), stat.S_IFLNK)
        self.assertEqual(
            archive.read("p0/p1/p2/link"),
            b"../../../var/mobile/Library/SpringBoard",
        )
        self.assertEqual(archive.read("payload"), b"canary")

    def test_books_manifest_contains_only_requested_assets(self) -> None:
        identifiers = ["../../source/link", "../../source/payload", "../../../target"]
        value = plistlib.loads(airlift_windows.build_books(identifiers))

        self.assertEqual(
            [row["Persistent ID"] for row in value["Books"]],
            identifiers,
        )
        self.assertEqual([row["Item ID"] for row in value["Books"]], ["1", "2", "3"])

    def test_windows_module_exposes_no_generic_existing_file_api(self) -> None:
        self.assertFalse(hasattr(airlift_windows, "read_file"))
        self.assertFalse(hasattr(airlift_windows, "write_file"))


if __name__ == "__main__":
    unittest.main()
