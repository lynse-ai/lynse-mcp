#!/usr/bin/env python3
"""Model Context Protocol server for Lynse meeting data."""

from __future__ import annotations

import argparse
import sys
from typing import Annotated, Any, Callable, Literal

try:
    from mcp.server import MCPServer
except ImportError:
    print(
        "Error: lynse-mcp requires MCP Python SDK 2.x. "
        "Install it with: python3 -m pip install -r requirements-mcp.txt",
        file=sys.stderr,
    )
    raise SystemExit(1)
from mcp.types import ToolAnnotations
from pydantic import Field

from lynse import LynseAPI, LynseAPIError

MCP_VERSION = "0.0.1"


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

    def __init__(self, api_factory: Callable[[], LynseAPI] = LynseAPI):
        self._api_factory = api_factory
        self._api: LynseAPI | None = None

    @property
    def api(self) -> LynseAPI:
        if self._api is None:
            self._api = self._api_factory()
        return self._api

    @staticmethod
    def _confirmed(confirm: bool, action: str) -> None:
        if not confirm:
            raise ValueError(
                f"{action} changes Lynse data. Obtain user confirmation, then call again "
                "with confirm=true."
            )

    @staticmethod
    def _call(operation: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return operation(*args, **kwargs)
        except LynseAPIError as exc:
            raise ValueError(exc.message) from exc

    def get_current_user(self) -> dict[str, Any]:
        """Get the current Lynse account profile."""
        return self._call(self.api.get_current_customer)

    def list_meetings(self, days: Days = 7) -> dict[str, Any]:
        """List meetings recorded during the last N days."""
        return self._call(self.api.list_files_by_time_range, days)

    def search_meetings(
        self,
        keyword: Annotated[str, Field(min_length=1, description="Meeting title keyword.")],
        page: Page = 1,
        page_size: PageSize = 20,
    ) -> dict[str, Any]:
        """Search meetings by title."""
        return self._call(self.api.search_files, keyword, page=page, page_size=page_size)

    def get_meeting(
        self,
        file_id: Identifier,
        content: Literal["info", "summary", "outline", "transcript"] = "info",
    ) -> dict[str, Any]:
        """Get meeting details, summary, outline, or plain-text transcript."""
        operations = {
            "info": self.api.get_file_info,
            "summary": self.api.get_conclusion,
            "outline": self.api.get_outline,
            "transcript": self.api.get_transcription_text,
        }
        return self._call(operations[content], file_id)

    def list_folders(self) -> dict[str, Any]:
        """List Lynse meeting folders."""
        return self._call(self.api.list_folders)

    def list_todos(
        self,
        status: Literal["all", "open", "done"] = "all",
        page: Page = 1,
        page_size: PageSize = 20,
    ) -> dict[str, Any]:
        """List Lynse todos, optionally filtered by completion status."""
        return self._call(self.api.list_todos, status=status, page_num=page, page_size=page_size)

    def plan_meeting_organization(
        self,
        days: Annotated[int | None, Field(ge=1, le=3650)] = None,
        include_no_conclusion: bool = False,
    ) -> dict[str, Any]:
        """Build a folder organization plan without changing Lynse data."""
        return self._call(
            self.api.organize_meetings,
            days=days,
            execute=False,
            include_no_conclusion=include_no_conclusion,
        )

    def apply_meeting_organization(
        self,
        confirm: bool,
        days: Annotated[int | None, Field(ge=1, le=3650)] = None,
        include_no_conclusion: bool = False,
    ) -> dict[str, Any]:
        """Create folders and move meetings according to the organization plan."""
        self._confirmed(confirm, "Applying the meeting organization plan")
        return self._call(
            self.api.organize_meetings,
            days=days,
            execute=True,
            yes=True,
            include_no_conclusion=include_no_conclusion,
        )

    def create_folder(
        self,
        name: Annotated[str, Field(min_length=1, description="New folder name.")],
        confirm: bool,
        color: Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")] = "#EEEEEE",
    ) -> dict[str, Any]:
        """Create a Lynse meeting folder."""
        self._confirmed(confirm, "Creating a folder")
        return self._call(self.api.create_folder, {"folderName": name, "color": color})

    def move_meetings(
        self,
        file_ids: Annotated[list[str], Field(min_length=1)],
        new_folder_id: Identifier,
        confirm: bool,
        old_folder_id: str = "",
    ) -> dict[str, Any]:
        """Move one or more meetings to a Lynse folder."""
        self._confirmed(confirm, "Moving meetings")
        return self._call(
            self.api.change_folder,
            {
                "oldFolderId": old_folder_id,
                "newFolderId": new_folder_id,
                "fileIds": file_ids,
            },
        )

    def delete_todos(
        self,
        todo_ids: Annotated[list[str], Field(min_length=1)],
        confirm: bool,
    ) -> dict[str, Any]:
        """Permanently delete selected Lynse todos."""
        self._confirmed(confirm, "Deleting todos")
        return self._call(self.api.delete_todos, todo_ids)

    def clear_completed_todos(self, confirm: bool) -> dict[str, Any]:
        """Permanently delete all completed Lynse todos."""
        self._confirmed(confirm, "Clearing completed todos")
        return self._call(self.api.clear_completed_todos)


def create_server(api_factory: Callable[[], LynseAPI] = LynseAPI) -> MCPServer:
    """Create a Lynse MCP server, optionally with an injected API for tests."""
    server = MCPServer(
        "lynse-mcp",
        version=MCP_VERSION,
        instructions=(
            "Access the user's Lynse meetings, summaries, transcripts, folders, and todos. "
            "Read operations are safe. Before calling a write tool, explain the exact change "
            "and obtain user confirmation. Never ask for or expose API keys through MCP tools."
        ),
    )
    tools = LynseMCPTools(api_factory)

    server.tool(title="Get current Lynse user", annotations=READ_ONLY)(tools.get_current_user)
    server.tool(title="List Lynse meetings", annotations=READ_ONLY)(tools.list_meetings)
    server.tool(title="Search Lynse meetings", annotations=READ_ONLY)(tools.search_meetings)
    server.tool(title="Get Lynse meeting content", annotations=READ_ONLY)(tools.get_meeting)
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


mcp = create_server()


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

    try:
        if args.transport == "stdio":
            mcp.run()
        else:
            mcp.run(transport="streamable-http", host=args.host, port=args.port)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
