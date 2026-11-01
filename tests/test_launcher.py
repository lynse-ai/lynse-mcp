import asyncio
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node is needed for the optional npm launcher")
class LauncherTests(unittest.TestCase):
    def test_repository_venv_works_from_another_directory(self):
        python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.is_file():
            self.skipTest("Repository virtual environment is not installed")
        env = {key: value for key, value in os.environ.items() if key != "LYNSE_MCP_PYTHON"}
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run(
                [NODE, str(ROOT / "bin/lynse-mcp.js"), "--help"],
                cwd=cwd, env=env, capture_output=True, text=True, timeout=15,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--transport", result.stdout)

    def test_invalid_python_override_does_not_silently_fall_back(self):
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run(
                [NODE, str(ROOT / "bin/lynse-mcp.js"), "--help"],
                env={**os.environ, "LYNSE_MCP_PYTHON": str(Path(cwd) / "missing-python")},
                capture_output=True, text=True, timeout=15,
            )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("LYNSE_MCP_PYTHON", result.stderr)

    def test_minimal_package_files_support_stdio_handshake_and_tool_calls(self):
        # Run a copy of only the shipped runtime, with no remote modules or .venv.
        with tempfile.TemporaryDirectory(prefix="lynse mcp ") as directory:
            package = Path(directory)
            (package / "bin").mkdir()
            for name in ("lynse_mcp.py", "lynse_auth.py", "lynse_ui.py", "web/dist/lynse-app.html", "bin/lynse-mcp.js"):
                (package / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, package / name)
            params = StdioServerParameters(
                command=NODE,
                args=[str(package / "bin/lynse-mcp.js")],
                cwd=directory,
                env={
                    "LYNSE_MCP_PYTHON": sys.executable,
                    "LYNSE_API_HOST": "https://example.test",
                    "LYNSE_API_KEY": "launcher-test-only",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )

            async def run():
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=10) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        result = await session.call_tool("get_auth_status", {})
                        resource = await session.read_resource("ui://lynse/meetings-v1.html")
                        return tools, result, resource

            tools, result, resource = asyncio.run(run())
        self.assertIn("get_meeting_audio", {tool.name for tool in tools.tools})
        self.assertIn("reset_lynse_key", {tool.name for tool in tools.tools})
        self.assertFalse(result.is_error)
        self.assertTrue(result.structured_content["configured"])
        self.assertNotIn("launcher-test-only", result.model_dump_json())
        self.assertEqual(resource.contents[0].mime_type, "text/html;profile=mcp-app")
        self.assertIn("<!doctype html>", resource.contents[0].text)


if __name__ == "__main__":
    unittest.main()
