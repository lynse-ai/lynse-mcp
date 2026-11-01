# Changelog

## 0.1.0 (2026-11-01)

### New Features

- Add an MCP Apps interface built on the official `@modelcontextprotocol/ext-apps` SDK, with `show_meetings`, `show_todos`, and `show_confirmation` display tools backed by `ui://` HTML resources.
- Serve the MCP Apps UI as a single self-contained HTML page that loads no external scripts, styles, or fonts.
- Add a local preview server for iterating on MCP Apps cards outside a host client.
- Add a vendor CLI that requires a pinned revision and checksum before replacing a vendored dependency.

### Changed

- Limit the npm package to the local stdio runtime and consolidate client setup in README.md.
- Separate remote dependencies and exclude Git and the optional OAuth test page from the production image.
- Let the Node launcher use the repository virtual environment or an explicit `LYNSE_MCP_PYTHON`.
- Add a three-file WorkBuddy OAuth connector ZIP with enforced limits below 20 MB.
- Depend on `lynse-ai/lynse-cli` v1.7.0 as the single Lynse API client implementation.
- Remove the copied `lynse.py`; this repository now contains only MCP transport, tools, and secure authorization adapters.

## 0.0.4 (2026-08-08)

### Changed

- Migrate the server entrypoint from `FastMCP` to `MCPServer` to match the official MCP Python SDK v2 stable APIs.
- Update streamable HTTP startup to pass transport host and port through `run()`, matching the current SDK contract.
- Switch tests to the first-class `Client(server)` pattern and v2 snake_case result fields such as `is_error`, `structured_content`, and `mime_type`.

## 0.0.3 (2026-07-30)

### New Features

- Add first-use Key authorization through a one-time local browser page.
- Add authorization status checks without exposing credentials.
- Add `reset_lynse_key` to replace a saved Key through the secure local authorization page after explicit confirmation.
- Add `get_meeting_audio`, backed by Lynse's presigned file download endpoint.

### Security

- Keep the existing Key active until a replacement Key has been successfully validated.
- Return audio as an MCP resource link instead of embedding large recording payloads.

### Changed

- Keep the MCP server and its tests compatible with the current MCP Python SDK 2.x APIs.

## 0.0.1 (2026-07-29)

### New Features

- Add the Lynse MCP server as a standalone project extracted from `lynse-cli`.
- Expose twelve MCP tools for meetings, folders, todos, and meeting organization.
- Require explicit confirmation for MCP write and destructive operations.
