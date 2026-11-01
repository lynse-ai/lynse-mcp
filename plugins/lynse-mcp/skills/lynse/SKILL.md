---
name: lynse
description: Work with the user's Lynse (灵光记) meeting notes through the lynse MCP tools — list and search meetings, read summaries/outlines/transcripts, fetch meeting audio, manage folders and todos. Use when the user asks about their meetings, 会议, 纪要, 转写, 待办, or wants a meeting recap or cleanup.
---

# Lynse Meeting Tools

The `lynse` MCP server is OAuth-protected; the account is bound by the
browser authorization the user completed. Credentials never appear in chat
or tool arguments. Read tools are available with the default `lynse:read`
scope; **write tools additionally require the `lynse:write` scope** and an
explicit `confirm=true` argument.

## Tool map

Read (always available):

| Need | Tool |
|---|---|
| Who am I connected as | `get_current_user` |
| Recent meetings | `list_meetings` (days, default 7, max 3650) |
| Find by title | `search_meetings` (keyword, page, page_size) |
| Details / 总结 / 大纲 / 转写 | `get_meeting` (file_id, content=info/summary/outline/transcript) |
| Audio file | `get_meeting_audio` (returns a resource link) |
| Transcription progress | `get_transcription_status` |
| Folder list | `list_folders` |
| Todos | `list_todos` (status=all/open/done) |
| Organization preview | `plan_meeting_organization` (read-only plan) |

Write (needs `lynse:write` scope AND `confirm=true`):

| Change | Tool |
|---|---|
| Transcribe a meeting | `transcribe_meeting` |
| Auto-transcribe new recordings | `auto_transcribe_new_recordings` |
| Execute organization plan | `apply_meeting_organization` |
| New folder | `create_folder` |
| Move meetings to a folder | `move_meetings` |
| Delete selected todos | `delete_todos` (destructive) |
| Clear completed todos | `clear_completed_todos` (destructive) |

Before any write: explain the exact change in plain language and get the
user's confirmation — never bundle several destructive changes silently.
`plan_meeting_organization` exists precisely so the user can review a plan
before `apply_meeting_organization` executes it.

`list_meetings` and `search_meetings` return meeting ids; pass those to
`get_meeting` / `get_meeting_audio`. Audio links are pre-signed and expire —
if a download fails, call `get_meeting_audio` again.

## Recipes

**Standup / weekly recap** — `list_meetings` for the range → `get_meeting`
with content=summary per meeting → synthesize one summary; pull action items
from outlines before falling back to transcripts.

**Find a specific discussion** — `search_meetings` by keyword → confirm with
`get_meeting` content=outline → quote the transcript section.

**Todo review** — `list_todos` with status=open, group by urgency, propose
deletions; only delete after explicit user approval.

**Meeting cleanup** — `plan_meeting_organization` → present the plan →
`apply_meeting_organization` with confirm=true only after approval.

**Transcript quotes** — always quote from content=transcript verbatim and
say which meeting it came from; do not paraphrase quotes.

## Failure handling

- `401` / unauthorized: the OAuth token expired — tell the user to re-open
  the server connection (re-authorize in the browser); do not ask for keys.
- "read permission only" on a write tool: the connection lacks
  `lynse:write` — the user must re-authorize with the write scope enabled.
- Empty meeting list: check the `days` window before assuming no data.
- Pre-signed audio URL expired: refresh via `get_meeting_audio`.
