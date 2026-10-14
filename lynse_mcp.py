#!/usr/bin/env python3
"""Model Context Protocol server for Lynse meeting data."""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Annotated, Any, Callable, Literal

try:
    from mcp.server import MCPServer
    from mcp.server.mcpserver import Context
except ImportError:
    print(
        "Error: lynse-mcp requires MCP Python SDK 2.x. "
        "Install it with: python3 -m pip install -r requirements-mcp.txt",
        file=sys.stderr,
    )
    raise SystemExit(1)
from mcp.types import CallToolResult, ResourceLink, ToolAnnotations
from pydantic import Field

from lynse import LynseAPI, LynseAPIError, TRANSCRIPTION_OPERATIONS
from lynse_auth import LocalAuthFlow, auth_status

MCP_VERSION = "0.0.4"


Days = Annotated[int, Field(ge=1, le=3650, description="Number of recent days to query.")]
Page = Annotated[int, Field(ge=1, description="One-based page number.")]
PageSize = Annotated[int, Field(ge=1, le=100, description="Maximum results per page.")]
Identifier = Annotated[str, Field(min_length=1, description="Lynse object ID.")]

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)
IDEMPOTENT_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=True,
)


class LynseMCPTools:
    """Small, testable adapter from MCP tools to the existing Lynse API."""

    def __init__(
        self,
        api_factory: Callable[[], LynseAPI] = LynseAPI,
        auth_status_fn: Callable[[], dict[str, object]] = auth_status,
        auth_flow_factory: Callable[[], LocalAuthFlow] = LocalAuthFlow,
    ):
        self._api_factory = api_factory
        self._auth_status = auth_status_fn
        self._auth_flow_factory = auth_flow_factory
        self._api: LynseAPI | None = None

    @property
    def api(self) -> LynseAPI:
        if self._api is None:
            self._api = self._api_factory()
        return self._api

    def get_auth_status(self) -> dict[str, object]:
        """Check whether Lynse credentials are configured, without exposing them."""
        return self._auth_status()

    async def authorize_lynse(self, ctx: Context) -> dict[str, object]:
        """Securely collect and validate a Lynse authorization key in the browser."""
        return await self._run_auth_flow(ctx, replacing=False)

    async def reset_lynse_key(
        self,
        ctx: Context,
        confirm: bool,
    ) -> dict[str, object]:
        """Replace the saved Lynse Key after validating a new Key in the browser."""
        self._confirmed(confirm, "Resetting the Lynse authorization Key")
        status = self._auth_status()
        if status.get("credential_source") == "environment":
            raise ValueError(
                "Lynse credentials come from environment variables. Update "
                "LYNSE_API_KEY or LYNSE_ACCESS_TOKEN and restart the MCP server."
            )
        return await self._run_auth_flow(ctx, replacing=True)

    async def _run_auth_flow(
        self,
        ctx: Context,
        *,
        replacing: bool,
    ) -> dict[str, object]:
        status = self._auth_status()
        if status["configured"] and not replacing:
            return {
                **status,
                "message": "Lynse authorization is already configured.",
            }

        flow = self._auth_flow_factory()
        url = flow.start()
        try:
            if replacing:
                message = (
                    "Open the secure local setup page to enter and validate a replacement "
                    "Lynse Key. The current Key remains active unless the replacement succeeds."
                )
            else:
                message = (
                    "Lynse needs an authorization Key. Open the secure local setup page; "
                    "the Key will not be sent through the chat or MCP tool arguments."
                )
            response = await ctx.elicit_url(
                message=message,
                url=url,
                elicitation_id=flow.elicitation_id,
            )
            if response.action != "accept":
                flow.stop()
                return {
                    **status,
                    "message": (
                        "Lynse Key replacement was cancelled; the existing authorization "
                        "is unchanged."
                        if replacing
                        else "Lynse authorization was declined or cancelled."
                    ),
                }
            result = await asyncio.to_thread(flow.wait)
        except BaseException:
            flow.stop()
            raise

        if result.configured:
            self._api = None
            return result.to_dict()
        if replacing:
            return {
                **status,
                "message": (
                    "Lynse Key replacement was not completed; the existing authorization "
                    f"is unchanged. {result.message}"
                ),
            }
        return result.to_dict()

    async def _ensure_authorized(self, ctx: Context) -> None:
        if self._auth_status()["configured"]:
            return
        result = await self.authorize_lynse(ctx)
        if not result.get("configured"):
            raise ValueError(str(result.get("message", "Lynse authorization is required.")))

    @staticmethod
    def _confirmed(confirm: bool, action: str) -> None:
        if not confirm:
            raise ValueError(
                f"{action} changes Lynse data. Obtain user confirmation, then call again "
                "with confirm=true."
            )

    @staticmethod
    async def _call(operation: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(operation, *args, **kwargs)
        except LynseAPIError as exc:
            raise ValueError(exc.message) from exc

    async def get_current_user(self, ctx: Context) -> dict[str, Any]:
        """Get the current Lynse account profile."""
        await self._ensure_authorized(ctx)
        return await self._call(self.api.get_current_customer)

    async def list_meetings(self, ctx: Context, days: Days = 7) -> dict[str, Any]:
        """List meetings recorded during the last N days."""
        await self._ensure_authorized(ctx)
        return await self._call(self.api.list_files_by_time_range, days)

    async def search_meetings(
        self,
        ctx: Context,
        keyword: Annotated[str, Field(min_length=1, description="Meeting title keyword.")],
        page: Page = 1,
        page_size: PageSize = 20,
    ) -> dict[str, Any]:
        """Search meetings by title."""
        await self._ensure_authorized(ctx)
        return await self._call(self.api.search_files, keyword, page=page, page_size=page_size)

    async def get_meeting(
        self,
        ctx: Context,
        file_id: Identifier,
        content: Literal["info", "summary", "outline", "transcript"] = "info",
    ) -> dict[str, Any]:
        """Get meeting details, summary, outline, or plain-text transcript."""
        await self._ensure_authorized(ctx)
        operations = {
            "info": self.api.get_file_info,
            "summary": self.api.get_conclusion,
            "outline": self.api.get_outline,
            "transcript": self.api.get_transcription_text,
        }
        return await self._call(operations[content], file_id)

    async def get_meeting_audio(
        self,
        ctx: Context,
        file_id: Identifier,
    ) -> CallToolResult:
        """Get a meeting audio file as an MCP resource link."""
        await self._ensure_authorized(ctx)
        result = await self._call(self.api.get_audio_file, file_id)
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict) or not data.get("url"):
            raise ValueError("Lynse did not return an audio file URL for this meeting.")

        raw_size = data.get("size")
        size = raw_size if isinstance(raw_size, int) and raw_size >= 0 else None
        file_name = str(data.get("fileName") or f"{file_id}.audio")
        mime_type = str(data.get("mimeType") or "audio/mpeg")
        resource = ResourceLink(
            type="resource_link",
            name=file_name,
            title=file_name,
            uri=str(data["url"]),
            description=(
                "Lynse meeting audio file. The signed download link may expire; "
                "call get_meeting_audio again to refresh it."
            ),
            mime_type=mime_type,
            size=size,
        )
        return CallToolResult(content=[resource], structured_content=result)

    async def transcribe_meeting(
        self,
        ctx: Context,
        file_id: Identifier,
        confirm: bool,
        template_id: Identifier | None = None,
        model_id: Identifier | None = None,
        language_id: Identifier | None = None,
    ) -> dict[str, Any]:
        """Submit cloud transcription and summary; may consume credits. A task ID means submitted, not completed."""
        self._confirmed(confirm, "Submitting transcription and summary (may consume credits)")
        await self._ensure_authorized(ctx)
        return await self._call(self.api.transcribe_file, file_id,
                                template_id=template_id, model_id=model_id, language_id=language_id)

    async def get_transcription_status(
        self,
        ctx: Context,
        file_ids: Annotated[list[Identifier], Field(min_length=1, max_length=TRANSCRIPTION_OPERATIONS['get_transcription_status']['max_batch'])],
    ) -> dict[str, Any]:
        """Read cloud transcription status for 1-100 file IDs without submitting tasks."""
        await self._ensure_authorized(ctx)
        return await self._call(self.api.get_transcription_status, file_ids)

    async def auto_transcribe_new_recordings(
        self,
        ctx: Context,
        confirm: bool,
        template_id: Identifier | None = None,
        model_id: Identifier | None = None,
        language_id: Identifier | None = None,
    ) -> dict[str, Any]:
        """Run one scan of new cloud audio. First call skips existing files; later calls may consume credits. Repeat via an authorized scheduler; this does not start a background watcher. Inspect needsReview before retrying."""
        self._confirmed(confirm, "Automatically transcribing new recordings (may consume credits)")
        await self._ensure_authorized(ctx)
        result = await self._call(self.api.auto_transcribe_new_recordings,
                                  template_id=template_id, model_id=model_id, language_id=language_id)
        # The shared CLI retains diagnostics locally; MCP must not expose server
        # filesystem paths or upstream error bodies to remote clients.
        data = dict(result.get('data') or {})
        data.pop('stateFile', None)
        data['needsReview'] = [
            {key: value for key, value in item.items() if key != 'error'}
            for item in data.get('needsReview', [])
        ]
        return {**result, 'data': data}

    async def list_folders(self, ctx: Context) -> dict[str, Any]:
        """List Lynse meeting folders."""
        await self._ensure_authorized(ctx)
        return await self._call(self.api.list_folders)

    async def list_todos(
        self,
        ctx: Context,
        status: Literal["all", "open", "done"] = "all",
        page: Page = 1,
        page_size: PageSize = 20,
    ) -> dict[str, Any]:
        """List Lynse todos, optionally filtered by completion status."""
        await self._ensure_authorized(ctx)
        return await self._call(self.api.list_todos, status=status, page_num=page, page_size=page_size)

    async def plan_meeting_organization(
        self,
        ctx: Context,
        days: Annotated[int | None, Field(ge=1, le=3650)] = None,
        include_no_conclusion: bool = False,
    ) -> dict[str, Any]:
        """Build a folder organization plan without changing Lynse data."""
        await self._ensure_authorized(ctx)
        return await self._call(
            self.api.organize_meetings,
            days=days,
            execute=False,
            include_no_conclusion=include_no_conclusion,
        )

    async def apply_meeting_organization(
        self,
        ctx: Context,
        confirm: bool,
        days: Annotated[int | None, Field(ge=1, le=3650)] = None,
        include_no_conclusion: bool = False,
    ) -> dict[str, Any]:
        """Create folders and move meetings according to the organization plan."""
        self._confirmed(confirm, "Applying the meeting organization plan")
        await self._ensure_authorized(ctx)
        return await self._call(
            self.api.organize_meetings,
            days=days,
            execute=True,
            yes=True,
            include_no_conclusion=include_no_conclusion,
        )

    async def create_folder(
        self,
        ctx: Context,
        name: Annotated[str, Field(min_length=1, description="New folder name.")],
        confirm: bool,
        color: Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")] = "#EEEEEE",
    ) -> dict[str, Any]:
        """Create a Lynse meeting folder."""
        self._confirmed(confirm, "Creating a folder")
        await self._ensure_authorized(ctx)
        return await self._call(self.api.create_folder, {"folderName": name, "color": color})

    async def move_meetings(
        self,
        ctx: Context,
        file_ids: Annotated[list[str], Field(min_length=1)],
        new_folder_id: Identifier,
        confirm: bool,
        old_folder_id: str = "",
    ) -> dict[str, Any]:
        """Move one or more meetings to a Lynse folder."""
        self._confirmed(confirm, "Moving meetings")
        await self._ensure_authorized(ctx)
        return await self._call(
            self.api.change_folder,
            {
                "oldFolderId": old_folder_id,
                "newFolderId": new_folder_id,
                "fileIds": file_ids,
            },
        )

    async def delete_todos(
        self,
        ctx: Context,
        todo_ids: Annotated[list[str], Field(min_length=1)],
        confirm: bool,
    ) -> dict[str, Any]:
        """Permanently delete selected Lynse todos."""
        self._confirmed(confirm, "Deleting todos")
        await self._ensure_authorized(ctx)
        return await self._call(self.api.delete_todos, todo_ids)

    async def clear_completed_todos(self, ctx: Context, confirm: bool) -> dict[str, Any]:
        """Permanently delete all completed Lynse todos."""
        self._confirmed(confirm, "Clearing completed todos")
        await self._ensure_authorized(ctx)
        return await self._call(self.api.clear_completed_todos)


def create_server(
    api_factory: Callable[[], LynseAPI] = LynseAPI,
    auth_status_fn: Callable[[], dict[str, object]] = auth_status,
    auth_flow_factory: Callable[[], LocalAuthFlow] = LocalAuthFlow,
    *,
    tools: LynseMCPTools | None = None,
    remote: bool = False,
    **server_options: Any,
) -> MCPServer:
    """Create a Lynse MCP server, optionally with an injected API for tests."""
    server = MCPServer(
        "lynse-mcp",
        instructions=(
            "Use the authenticated user's Lynse account within the granted scopes. "
            "Before a write or deletion, explain the exact change and obtain confirmation. "
            "If authentication expires, reconnect through the website's OAuth flow."
        ) if remote else (
            "Access the user's Lynse meetings, audio files, summaries, transcripts, "
            "folders, and todos. "
            "On first use, check authorization status. If credentials are missing, call "
            "authorize_lynse so the user can enter the Key through a secure local browser page. "
            "To replace a configured Key, explain that the validated replacement will overwrite "
            "the saved credentials, obtain confirmation, then call reset_lynse_key. "
            "Read operations are safe. Before calling a write tool, explain the exact change "
            "and obtain user confirmation. Never ask for or expose API keys in chat or tool arguments."
        ),
        **server_options,
    )
    tools = tools or LynseMCPTools(api_factory, auth_status_fn, auth_flow_factory)

    server.tool(title="Check Lynse authorization", annotations=READ_ONLY)(tools.get_auth_status)
    if not remote:
        server.tool(title="Authorize Lynse", annotations=IDEMPOTENT_WRITE)(tools.authorize_lynse)
        server.tool(title="Reset Lynse Key", annotations=WRITE)(tools.reset_lynse_key)
    server.tool(title="Get current Lynse user", annotations=READ_ONLY)(tools.get_current_user)
    server.tool(title="List Lynse meetings", annotations=READ_ONLY)(tools.list_meetings)
    server.tool(title="Search Lynse meetings", annotations=READ_ONLY)(tools.search_meetings)
    server.tool(title="Get Lynse meeting content", annotations=READ_ONLY)(tools.get_meeting)
    server.tool(title="Get Lynse meeting audio file", annotations=READ_ONLY)(
        tools.get_meeting_audio
    )
    for operation in TRANSCRIPTION_OPERATIONS.values():
        server.tool(name=operation['mcp_tool'], title=operation['title'],
                    annotations=WRITE if operation['write'] else READ_ONLY)(
                        getattr(tools, operation['mcp_tool']))
    server.tool(title="List Lynse folders", annotations=READ_ONLY)(tools.list_folders)
    server.tool(title="List Lynse todos", annotations=READ_ONLY)(tools.list_todos)
    server.tool(title="Plan meeting organization", annotations=READ_ONLY)(
        tools.plan_meeting_organization
    )
    server.tool(title="Apply meeting organization", annotations=WRITE)(
        tools.apply_meeting_organization
    )
    server.tool(title="Create Lynse folder", annotations=WRITE)(tools.create_folder)
    server.tool(title="Move Lynse meetings", annotations=IDEMPOTENT_WRITE)(tools.move_meetings)
    server.tool(title="Delete Lynse todos", annotations=DESTRUCTIVE)(tools.delete_todos)
    server.tool(title="Clear completed Lynse todos", annotations=DESTRUCTIVE)(
        tools.clear_completed_todos
    )
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Lynse MCP server.")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="MCP transport (default: stdio).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host.")
    parser.add_argument("--port", type=int, default=8000, help="HTTP bind port.")
    args = parser.parse_args()
    server = create_server()

    try:
        if args.transport == "stdio":
            server.run()
        else:
            server.run("streamable-http", host=args.host, port=args.port)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
