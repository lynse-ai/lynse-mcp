"""Portable MCP Apps resources and read-only adapters for Lynse."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field, StringConstraints

APP_MIME_TYPE = "text/html;profile=mcp-app"
APP_URIS = {view: f"ui://lynse/{view}-v1.html" for view in ("meetings", "todos", "confirmation")}
APP_HTML = Path(__file__).resolve().parent / "web" / "dist" / "lynse-app.html"
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
Page = Annotated[int, Field(ge=1, le=1000)]
PageSize = Annotated[int, Field(ge=1, le=100)]


class ConfirmationOption(BaseModel):
    id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    description: Annotated[str, StringConstraints(max_length=1000)] = ""


def media_origins() -> list[str]:
    """Deployment-owned media allowlist; tool input never controls the iframe CSP."""
    defaults = ["https://*.lynse.cn", "https://*.lynse.ai", "https://*.aliyuncs.com"]
    extra = os.environ.get("LYNSE_MCP_MEDIA_ORIGINS", "")
    for origin in extra.split(","):
        origin = origin.strip().rstrip("/")
        if not origin:
            continue
        parsed = urlsplit(origin)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("LYNSE_MCP_MEDIA_ORIGINS must contain comma-separated HTTPS origins")
        if origin not in defaults:
            defaults.append(origin)
    return defaults


def _data(result: dict[str, Any]) -> Any:
    if result.get("code") not in (None, 0, 200, "0", "200"):
        raise ValueError("Lynse request failed; please try again later.")
    return result.get("data")


def _items(result: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
    data = _data(result)
    total = result.get("total")
    if isinstance(data, dict):
        total = data.get("total", total)
        data = next((data[key] for key in ("records", "list", "rows", "items")
                     if isinstance(data.get(key), list)), None)
        if data is None:
            raise ValueError("Lynse returned an unexpected list format.")
    if data is None:
        data = []
    if not isinstance(data, list):
        raise ValueError("Lynse returned an unexpected list format.")
    try:
        count = max(0, int(total)) if total is not None else None
    except (ValueError, TypeError):
        count = None
    return [item for item in data if isinstance(item, dict)], count


def _string(item: dict[str, Any], *keys: str) -> str:
    return next((str(item[key]) for key in keys if item.get(key) not in (None, "")), "")


def _meeting(item: dict[str, Any]) -> dict[str, Any]:
    try:
        duration = float(item.get("bizDuration") or item.get("duration") or 0)
        duration = duration if math.isfinite(duration) and duration > 0 else 0
    except (ValueError, TypeError):
        duration = 0
    return {
        "id": _string(item, "id", "fileId"),
        "title": _string(item, "originalFilename", "filename", "fileName", "name") or "未命名会议",
        "recordedAt": _string(item, "recordStartTime", "createTime"),
        "duration": duration,
        "folder": _string(item, "folderName"),
    }


def _todo(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _string(item, "todoId", "id"),
        "content": _string(item, "todoContent", "content") or "未命名待办",
        "completed": item.get("isCompleted") in (True, 1, "1", "true"),
        "dueAt": _string(item, "expectedCompleteTime"),
        "fileId": _string(item, "fileId"),
        "meetingTitle": _string(item, "originalFilename", "fileName", "meetingTitle"),
    }


def _text_content(value: Any) -> str:
    if isinstance(value, str):
        return value if value.strip() else ""
    if isinstance(value, list):
        return "\n\n".join(filter(None, (_text_content(item) for item in value)))
    if isinstance(value, dict):
        for key in ("conclusionText", "outlineText", "content", "conclusion", "summary", "markdown", "outline", "text"):
            if key in value:
                return _text_content(value[key])
        parts = [_string(value, "title", "name"), _text_content(value.get("children"))]
        return "\n".join(part for part in parts if part.strip())
    return ""


def _result(payload: dict[str, Any], fallback: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=fallback)], structured_content=payload)


class LynseAppTools:
    def __init__(self, tools):
        # Reuse the injected local or account-scoped adapter, including OAuth checks.
        self.tools = tools

    async def meetings(
        self, ctx: Context,
        days: Annotated[int, Field(ge=1, le=3650)] = 7,
        keyword: Annotated[str, Field(max_length=200)] = "",
        page: Page = 1, page_size: PageSize = 20,
        file_id: Identifier | None = None,
    ) -> CallToolResult:
        """Show an interactive meeting list with details, summary, transcript and audio. Optional file_id opens one meeting. All reads use the current authenticated account."""
        keyword = keyword.strip()
        query = {"days": days, "keyword": keyword, "page": page, "pageSize": page_size}
        if file_id:
            info = _data(await self.tools.get_meeting(ctx, file_id, "info"))
            if not isinstance(info, dict):
                raise ValueError("Lynse did not return meeting details.")
            meeting = _meeting({**info, "id": file_id})
            meetings, total, has_next = [meeting], 1, False
        elif keyword:
            items, total = _items(await self.tools.search_meetings(ctx, keyword, page, page_size))
            meetings = [_meeting(item) for item in items]
            has_next = page * page_size < total if total is not None else len(items) == page_size
        else:
            items, _ = _items(await self.tools.list_meetings(ctx, days))
            total = len(items)
            start = (page - 1) * page_size
            meetings = [_meeting(item) for item in items[start:start + page_size]]
            has_next = start + page_size < total
        payload = {"view": "meetings", "meetings": meetings, "total": total,
                   "hasNext": has_next, "query": query, "selectedId": file_id}
        fallback = "会议列表：\n" + ("\n".join(f"- {m['title']}（ID: {m['id']}）" for m in meetings) or "暂无会议。")
        return _result(payload, fallback)

    async def meeting(
        self, ctx: Context, file_id: Identifier,
        content: Literal["info", "summary", "outline", "transcript"] = "info",
    ) -> CallToolResult:
        """Read meeting content for an existing UI without opening another widget."""
        data = _data(await self.tools.get_meeting(ctx, file_id, content))
        if content == "info":
            if not isinstance(data, dict):
                raise ValueError("Lynse did not return meeting details.")
            payload = {"meeting": _meeting({**data, "id": file_id})}
        else:
            payload = {"text": _text_content(data)}
        return _result({"fileId": file_id, "content": content, **payload}, "已读取会议内容。")

    async def todos(
        self, ctx: Context, status: Literal["all", "open", "done"] = "all",
        page: Page = 1, page_size: PageSize = 20,
    ) -> CallToolResult:
        """Show the current account's todo list with status filters, deadlines, pagination and source meetings. This is read-only and does not change completion status."""
        items, total = _items(await self.tools.list_todos(ctx, status, page, page_size))
        todos = [_todo(item) for item in items]
        payload = {"view": "todos", "todos": todos, "total": total,
                   "hasNext": page * page_size < total if total is not None else len(items) == page_size,
                   "query": {"status": status, "page": page, "pageSize": page_size}}
        fallback = "待办列表：\n" + ("\n".join(
            f"- {'[已完成]' if t['completed'] else '[待完成]'} {t['content']}" for t in todos) or "暂无待办。")
        return _result(payload, fallback)

    async def confirmation(
        self, question: Text,
        options: Annotated[list[ConfirmationOption], Field(min_length=2, max_length=12)],
        description: Annotated[str, Field(max_length=4000)] = "",
    ) -> CallToolResult:
        """Present an A/B/C single-choice confirmation. Use stable option IDs and concrete action descriptions. Returns awaiting_user, NOT a choice or authorization. Wait for the user's subsequent message; merely rendering this UI never authorizes a write. Without UI, ask the user to reply with the option ID in chat."""
        ids = [option.id for option in options]
        if len(set(ids)) != len(ids):
            raise ValueError("Confirmation option IDs must be unique.")
        payload = {"view": "confirmation", "requestId": str(uuid4()), "status": "awaiting_user",
                   "question": question, "description": description,
                   "options": [option.model_dump() for option in options]}
        fallback = f"{question}\n{description}\n" + "\n".join(
            f"{option.id}. {option.label}" + (f" — {option.description}" if option.description else "")
            for option in options)
        return _result(payload, fallback + "\n请在界面选择并确认，或在聊天中回复选项编号；当前尚未确认。")


def register_apps(server: MCPServer, tools) -> None:
    adapter = LynseAppTools(tools)
    for view, uri in APP_URIS.items():
        server.resource(
            uri, name=f"lynse-{view}", title=f"Lynse {view}", mime_type=APP_MIME_TYPE,
            meta={"ui": {"prefersBorder": True, "csp": {"resourceDomains": media_origins(), "connectDomains": []}}},
        )(read_app_html)
    for name, view, handler in (
        ("show_meetings", "meetings", adapter.meetings),
        ("show_todos", "todos", adapter.todos),
        ("show_confirmation", "confirmation", adapter.confirmation),
    ):
        server.tool(name=name, title=f"Show Lynse {view}", annotations=READ_ONLY,
                    meta={"ui": {"resourceUri": APP_URIS[view], "visibility": ["model", "app"]}})(handler)
    # UI interactions use data tools, so filtering / navigation does not create new widgets.
    for name, handler in (("read_meetings_view", adapter.meetings),
                          ("read_meeting_view", adapter.meeting),
                          ("read_todos_view", adapter.todos)):
        server.tool(name=name, annotations=READ_ONLY, meta={"ui": {"visibility": ["app"]}})(handler)


def read_app_html() -> str:
    try:
        return APP_HTML.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ValueError("MCP Apps UI bundle is missing. Run npm ci && npm run build:ui.") from None
