"""Tests for finding the per-platform installers and building version.json (no R2 calls)."""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from publish_release_to_r2 import build_manifest, find_installers  # noqa: E402

FILES = ["Tasador-v0.2.0-windows-x64-setup.exe", "Tasador-v0.2.0-macos-arm64.dmg"]


class TestPublish(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dist = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, names):
        for name in names:
            (self.dist / name).write_bytes(name.encode())

    def test_finds_one_installer_per_platform(self):
        self.make(FILES + ["Tasador-v0.1.0-macos-arm64.dmg"])
        found = find_installers(self.dist, "0.2.0")
        self.assertEqual({p: f.name for p, f in found.items()}, {
            "windows-x64": "Tasador-v0.2.0-windows-x64-setup.exe",
            "macos-arm64": "Tasador-v0.2.0-macos-arm64.dmg",
        })

    def test_missing_platform_fails(self):
        self.make(FILES[:1])
        with self.assertRaises(SystemExit) as ctx:
            find_installers(self.dist, "0.2.0")
        self.assertIn("macos-arm64.dmg", str(ctx.exception))

    def test_manifest_keeps_windows_shape_and_adds_macos(self):
        self.make(FILES)
        manifest = build_manifest("0.2.0", find_installers(self.dist, "0.2.0"), "https://cdn.example", "tasador")
        self.assertEqual(manifest["version"], "0.2.0")
        win, mac = manifest["windows_x64"], manifest["macos_arm64"]
        self.assertEqual(win["url"], "https://cdn.example/tasador/v0.2.0/Tasador-v0.2.0-windows-x64-setup.exe")
        self.assertEqual(win["latest_url"], "https://cdn.example/tasador/latest/Tasador-windows-x64-setup.exe")
        self.assertEqual(mac["latest_url"], "https://cdn.example/tasador/latest/Tasador-macos-arm64.dmg")
        self.assertEqual(mac["sha256"], hashlib.sha256(FILES[1].encode()).hexdigest())
        self.assertEqual(manifest["release_page"], "https://github.com/luisenriquecgongora/tasador/releases/tag/v0.2.0")


if __name__ == "__main__":
    unittest.main()
