import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.vendor_cli import CLI_REQUIREMENT, prepare, use_wheel


class VendoredCLITests(unittest.TestCase):
    def test_source_revision_and_checksum_are_required_before_replacing_dependency(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            requirements = root / "requirements.txt"
            original = f"requests>=2.28.0\n{CLI_REQUIREMENT}\n"
            requirements.write_text(original)
            vendor = root / "vendor"
            vendor.mkdir()
            wheel = vendor / "lynse_cli-1.8.5-py3-none-any.whl"
            wheel.write_bytes(b"test-wheel")
            manifest = {"requirement": "unexpected source", "revision": "a" * 40,
                        "wheel": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
            metadata = vendor / "lynse-cli-source.json"
            metadata.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "source"):
                use_wheel(requirements, vendor)
            self.assertEqual(requirements.read_text(), original)
            manifest["requirement"] = CLI_REQUIREMENT
            manifest["revision"] = "main"
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

    def test_local_worktree_wheel_requires_delta_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            requirements = root / "requirements.txt"
            original = f"requests>=2.28.0\n{CLI_REQUIREMENT}\n"
            requirements.write_text(original)
            vendor = root / "vendor"
            vendor.mkdir()
            wheel = vendor / "lynse_cli-1.8.5-py3-none-any.whl"
            wheel.write_bytes(b"test-wheel")
            manifest = {"requirement": CLI_REQUIREMENT, "revision": "a" * 40,
                        "source": "local-worktree", "wheel": wheel.name,
                        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
            metadata = vendor / "lynse-cli-source.json"
            metadata.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "delta fingerprint"):
                use_wheel(requirements, vendor)
            self.assertEqual(requirements.read_text(), original)
            manifest["worktree_sha256"] = "b" * 64
            metadata.write_text(json.dumps(manifest))
            use_wheel(requirements, vendor)
            self.assertIn(wheel.name, requirements.read_text())

    def test_prepare_worktree_builds_from_checkout_and_records_delta(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            checkout = root / "cli"
            checkout.mkdir()
            (checkout / "pyproject.toml").write_text(
                '[build-system]\nrequires = ["setuptools"]\n'
                'build-backend = "setuptools.build_meta"\n'
                '[project]\nname = "lynse-cli"\nversion = "1.8.5"\n')
            (checkout / "lynse.py").write_text('CLI_VERSION = "1.8.5"\n')
            requirements = root / "requirements.txt"
            requirements.write_text(f"requests>=2.28.0\n{CLI_REQUIREMENT}\n")
            vendor = root / "vendor"

            def fake_run(command, **_kwargs):
                if command[:3] == [sys.executable, "-m", "pip"]:
                    (vendor / "lynse_cli-1.8.5-py3-none-any.whl").write_bytes(b"wheel")
                return None

            with patch("scripts.vendor_cli.subprocess.run", side_effect=fake_run), \
                 patch("scripts.vendor_cli.subprocess.check_output",
                       side_effect=["a" * 40 + "\n", b"uncommitted delta"]):
                prepare(requirements, str(checkout), vendor, worktree=True)

            manifest = json.loads((vendor / "lynse-cli-source.json").read_text())
            self.assertEqual(manifest["source"], "local-worktree")
            self.assertEqual(manifest["revision"], "a" * 40)
            self.assertEqual(manifest["worktree_sha256"],
                             hashlib.sha256(b"uncommitted delta").hexdigest())
            self.assertEqual(manifest["wheel"], "lynse_cli-1.8.5-py3-none-any.whl")
            # Building from a worktree snapshot must never touch the real checkout.
            self.assertEqual((checkout / "lynse.py").read_text(), 'CLI_VERSION = "1.8.5"\n')
            self.assertEqual(sorted(p.name for p in checkout.iterdir()),
                             ["lynse.py", "pyproject.toml"])
