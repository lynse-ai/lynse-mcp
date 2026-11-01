import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.vendor_cli import use_wheel


class VendoredCLITests(unittest.TestCase):
    def test_pinned_revision_and_checksum_are_required_before_replacing_dependency(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            requirements = root / "requirements.txt"
            original = "requests>=2.28.0\nlynse-cli @ git+https://github.com/lynse-ai/lynse-cli.git@" + "a" * 40 + "\n"
            requirements.write_text(original)
            vendor = root / "vendor"
            vendor.mkdir()
            wheel = vendor / "lynse_cli-1.8.5-py3-none-any.whl"
            wheel.write_bytes(b"test-wheel")
            manifest = {"revision": "b" * 40, "wheel": wheel.name,
                        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
            metadata = vendor / "lynse-cli-source.json"
            metadata.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "revision"):
                use_wheel(requirements, vendor)
            self.assertEqual(requirements.read_text(), original)
            manifest["revision"] = "a" * 40
            metadata.write_text(json.dumps(manifest))
            wheel.write_bytes(b"changed-wheel")
            with self.assertRaisesRegex(ValueError, "checksum"):
                use_wheel(requirements, vendor)
            self.assertEqual(requirements.read_text(), original)
            wheel.write_bytes(b"test-wheel")
            use_wheel(requirements, vendor)
            self.assertNotIn("git+https", requirements.read_text())
            self.assertIn(wheel.name, requirements.read_text())
            self.assertIn("requests>=2.28.0", requirements.read_text())
