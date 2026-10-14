import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

from scripts.package_workbuddy import build_package, FILES, MAX_BYTES, ROOT


class WorkBuddyPackageTests(unittest.TestCase):
    def test_upload_contains_only_required_files_and_stays_under_20mb(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            shutil.copytree(ROOT / "integrations/workbuddy", source)
            (source / ".env").write_text("PRIVATE_TOKEN=must-not-be-shipped")
            output = root / "connector.zip"
            result = build_package(source, output)
            self.assertLess(result["bytes"], MAX_BYTES)
            self.assertLess(result["unpacked_bytes"], MAX_BYTES)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), set(FILES))
                self.assertIsNone(archive.testzip())
                config = json.loads(archive.read("mcp.json"))
                self.assertEqual(len(config["mcpServers"]), 1)
                server = config["mcpServers"]["lynse"]
                self.assertEqual(server["type"], "streamableHttp")
                self.assertNotIn("headers", server)
                self.assertNotIn("command", server)
                self.assertTrue(archive.read("icon.png").startswith(b"\x89PNG\r\n\x1a\n"))

    def test_production_override_is_validated_without_modifying_source(self):
        source = ROOT / "integrations/workbuddy"
        original = (source / "mcp.json").read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "connector.zip"
            result = build_package(source, output, url="https://mcp.lynse.cn/mcp")
            self.assertEqual(result["url"], "https://mcp.lynse.cn/mcp")
            for invalid in ("http://example.test/mcp", "https://token@example.test/mcp", "https://example.test/mcp?token=secret"):
                with self.assertRaises(ValueError):
                    build_package(source, output, url=invalid)
        self.assertEqual((source / "mcp.json").read_bytes(), original)

    def test_oversized_runtime_or_asset_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            shutil.copytree(ROOT / "integrations/workbuddy", source)
            with (source / "icon.png").open("wb") as icon:
                icon.truncate(MAX_BYTES)
            with self.assertRaisesRegex(ValueError, "below 20 MB"):
                build_package(source, root / "connector.zip")
            self.assertFalse((root / "connector.zip").exists())


if __name__ == "__main__":
    unittest.main()
