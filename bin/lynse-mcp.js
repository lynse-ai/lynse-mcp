#!/usr/bin/env node

const { spawnSync } = require("child_process");
const path = require("path");

const root = path.resolve(__dirname, "..");
const serverPy = path.join(root, "lynse_mcp.py");
const userArgs = process.argv.slice(2);
const localPython = process.platform === "win32"
  ? path.join(root, ".venv", "Scripts", "python.exe")
  : path.join(root, ".venv", "bin", "python");
const candidates = process.env.LYNSE_MCP_PYTHON
  ? [{ command: process.env.LYNSE_MCP_PYTHON, prefixArgs: [] }]
  : [{ command: localPython, prefixArgs: [] }, ...(process.platform === "win32"
  ? [
      { command: "python", prefixArgs: [] },
      { command: "py", prefixArgs: ["-3"] },
      { command: "python3", prefixArgs: [] },
    ]
  : [
      { command: "python3", prefixArgs: [] },
      { command: "python", prefixArgs: [] },
    ])];

for (const candidate of candidates) {
  const versionCheck = spawnSync(
    candidate.command,
    [
      ...candidate.prefixArgs,
      "-c",
      "import sys; from mcp.server import MCPServer; from lynse import LynseAPI; "
        + "sys.exit(0 if sys.version_info >= (3, 11) else 1)",
    ],
    { stdio: "ignore" },
  );

  if (versionCheck.error && versionCheck.error.code === "ENOENT") {
    continue;
  }
  if (versionCheck.error) {
    console.error(`Error: failed to run ${candidate.command}: ${versionCheck.error.message}`);
    process.exit(1);
  }
  if (versionCheck.status !== 0) {
    continue;
  }

  const result = spawnSync(
    candidate.command,
    [...candidate.prefixArgs, serverPy, ...userArgs],
    { stdio: "inherit" },
  );
  if (result.signal) {
    process.kill(process.pid, result.signal);
  }
  process.exit(result.status ?? 0);
}

console.error(
  "Error: Python 3.11+ with MCP SDK 2.x and lynse-cli was not found. "
    + "Install requirements-mcp.txt, then set LYNSE_MCP_PYTHON to that Python executable.",
);
process.exit(1);
