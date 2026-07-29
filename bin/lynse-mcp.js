#!/usr/bin/env node

const { spawnSync } = require("child_process");
const path = require("path");

const serverPy = path.resolve(__dirname, "..", "lynse_mcp.py");
const userArgs = process.argv.slice(2);
const candidates = process.platform === "win32"
  ? [
      { command: "python", prefixArgs: [] },
      { command: "py", prefixArgs: ["-3"] },
      { command: "python3", prefixArgs: [] },
    ]
  : [
      { command: "python3", prefixArgs: [] },
      { command: "python", prefixArgs: [] },
    ];

for (const candidate of candidates) {
  const versionCheck = spawnSync(
    candidate.command,
    [
      ...candidate.prefixArgs,
      "-c",
      "import sys; from mcp.server import MCPServer; "
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
  "Error: Python 3.11+ with MCP SDK 2.x was not found. "
    + "Install requirements-mcp.txt in the selected Python environment, then retry.",
);
process.exit(1);
