# Changelog

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
