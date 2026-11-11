# Changelog

## 0.2.1 (2026-11-11)

### New Features

- Add three recording-mark tools: `get_meeting_marks` (playback offsets, text marks and mark images for a recording), `list_mark_images` (mark images by recording MD5) and `get_mark_image` (temporary signed resource link), backed by the new lynse-cli mark APIs.

### Changed

- `get_meeting` now returns only the meeting conclusion by default (`content="conclusion"`, first conclusion, no mind map) instead of file info, and exposes `all_conclusions`, `include_mind_map`, `max_chars` and `offset` so other payloads are requested explicitly and long ones page through.
- Cap every `get_meeting` result at `max_chars` (8,000 by default, 50,000 maximum) and replace inline base64 images — the conclusion's embedded mind-map PNG — with `[inline image omitted]`; one 99-minute meeting drops from ~147k characters to ~6k.
- Build the vendored lynse-cli wheel from a local checkout with `scripts/vendor_cli.py prepare --worktree`, recording the worktree delta fingerprint next to the source revision.

### Fixed

- Report the released version in the MCP `initialize` handshake (`serverInfo.version`) instead of the SDK's empty default.
- Replay a duplicate authorization-code exchange idempotently: a `workbuddy://` callback delivered twice now returns the same token bundle instead of `invalid_grant`, while a different client, a revoked grant and an expired code still fail closed.

## 0.2.0 (2026-11-09)

### New Features

- Add an account console for authorization records: list, revoke, and delete granted applications through the website bridge API (`/platform/oauth/api/authorizations*`), with revoked and expired records retained until user deletion and an optional test-UI management page.
- Give every authorization its own absolute lifetime (90 days by default, `LYNSE_MCP_GRANT_DAYS`) instead of inheriting the browser login session's ≤24h expiry, so third-party clients reconnect on the order of months rather than daily.

### Changed

- Rotate the refresh token on every use and slide a fresh 30-day idle window (`LYNSE_MCP_REFRESH_IDLE_DAYS`), always capped by the grant's absolute expiry; idle connections expire after the unused window.
- Revoke the whole grant family when a rotated refresh token is replayed (RFC 9700), while forged unknown tokens are rejected without side effects.
- Refresh the upstream credentials of a user's active grants whenever they sign in on the website again, so long-lived connections recover from the ≤24h upstream token until the account backend offers renewable credentials.
- Extend the authorization-page cookie and browser login state to the full session lifetime (up to 24h) instead of one hour.
- Keep DCR client records for 365 days so they outlive the grants they authorize.
- Scale the OAuth store for many users: per-subject and consumed-token indexes replace full-table scans, token lookups no longer take the write lock, capacity raised to 500k rows, and write transactions preserve the SDK's frozen-dataclass token errors on Python 3.14.

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
