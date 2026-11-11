# Lynse MCP

Connect ZCode to [Lynse (灵光记)](https://www.lynse.ai) — the AI meeting notes
app — so the agent can work with your meetings, summaries, outlines,
transcripts, audio, folders, and todos.

## What the agent gets

The plugin registers one remote MCP server (`lynse`) with 16 tools:

**Read** (included in the default `lynse:read` scope):

- `get_current_user` — the connected account profile
- `list_meetings` / `search_meetings` — recent meetings, search by title
- `get_meeting` — details, summary (总结), outline (大纲), or transcript (转写)
- `get_meeting_audio` — a pre-signed audio download link (resource link)
- `get_transcription_status` — transcription progress for a meeting
- `list_folders` / `list_todos` / `plan_meeting_organization`

**Write** (only after the `lynse:write` scope is granted at authorization):

- `transcribe_meeting` / `auto_transcribe_new_recordings` — start transcription
- `apply_meeting_organization` — execute a folder organization plan
- `create_folder` / `move_meetings`
- `delete_todos` / `clear_completed_todos` (destructive, confirm-gated)

Every write tool requires an explicit `confirm=true` argument on top of the
scope, so the agent must state the change and get user approval first.

A bundled `lynse` skill teaches the agent meeting-recap and todo-review
workflows.

## Authorization

The server is OAuth-protected. When you add the server, ZCode opens the
Lynse authorization page (`www.lynse.cn/mcp/authorize`) where you sign in
with WeChat or phone number and approve the `meetings:read` scope. Lynse
credentials never appear in chat, tool arguments, or plugin configuration;
only revocable OAuth tokens are used.

## Side effects and dependencies

- **Network**: talks only to `https://www.lynse.cn` (the MCP endpoint and
  OAuth authorization server). Meeting audio links point to Lynse's object
  storage.
- **MCP server**: provides the remote `lynse` MCP server operated by lynse.ai
  (see [DEPLOY docs](https://github.com/lynse-ai/lynse-mcp)).
- **No file writes, no command execution, no hooks.** The plugin bundles no
  binaries and no third-party code. Changes to Lynse data go through the
  MCP tools with per-call confirmation.
- **Scope-gated**: the connection requests `lynse:read`; write tools only
  work when the user grants `lynse:write` on the authorization page. Local
  (stdio) deployment with local credentials is documented in the
  [lynse-mcp repository](https://github.com/lynse-ai/lynse-mcp).

## Requirements

- A Lynse account with at least one recorded meeting.
- Outbound HTTPS access to `www.lynse.cn`.

## License

MIT. This plugin is maintained in the
[lynse-mcp repository](https://github.com/lynse-ai/lynse-mcp).
