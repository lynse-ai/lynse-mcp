"""Loopback-only MCP Apps preview, using fixtures through the real MCP server."""
from __future__ import annotations

import argparse
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mcp import Client
from lynse_mcp import create_server
from lynse_ui import APP_HTML

MEETINGS = [
    {"id": "meeting-1", "originalFilename": "产品设计周会", "recordStartTime": "2026-10-16T10:00:00", "bizDuration": 2340, "folderName": "产品与设计"},
    {"id": "meeting-2", "originalFilename": "与客户一起，梳理下一步", "recordStartTime": "2026-10-15T14:30:00", "bizDuration": 1680, "folderName": "客户访谈"},
    {"id": "meeting-3", "originalFilename": "灵光记 · 秋季版本评审", "recordStartTime": "2026-10-14T11:00:00", "bizDuration": 3120, "folderName": "产品与设计"},
]
TODOS = [
    {"todoId": "todo-1", "todoContent": "整理会议回顾的交互方案，周五前同步给设计团队", "isCompleted": 0, "expectedCompleteTime": "2026-10-18", "fileId": "meeting-1", "fileName": "产品设计周会"},
    {"todoId": "todo-2", "todoContent": "跟进客户反馈，确认音频播放与转写的使用场景", "isCompleted": 0, "expectedCompleteTime": "2026-10-20", "fileId": "meeting-2", "fileName": "与客户一起，梳理下一步"},
    {"todoId": "todo-3", "todoContent": "同步本周的版本评审记录", "isCompleted": 1, "expectedCompleteTime": "2026-10-15", "fileId": "meeting-3", "fileName": "灵光记 · 秋季版本评审"},
    {"todoId": "todo-4", "todoContent": "补充下一次访谈的问题清单", "isCompleted": 0},
]
CONFIRMATION = {
    "question": "这些会议，你想怎样整理？",
    "description": "选择适合你的方式。确认后，我会根据你的选择继续处理。",
    "options": [
        {"id": "A", "label": "按项目归档", "description": "将相关会议归入对应的项目文件夹，方便持续跟进。"},
        {"id": "B", "label": "按时间整理", "description": "以周或月为线索，保留讨论发生的先后顺序。"},
        {"id": "C", "label": "先看看整理建议", "description": "先生成建议清单，确认具体内容后再做调整。"},
    ],
}


class PreviewAPI:
    def __init__(self, port):
        self.port = port

    def list_files_by_time_range(self, _days):
        return {"code": 200, "data": MEETINGS}

    def search_files(self, keyword, page=1, page_size=20):
        items = [item for item in MEETINGS if keyword in item["originalFilename"]]
        start = (page - 1) * page_size
        return {"code": 200, "data": {"records": items[start:start + page_size], "total": len(items)}}

    def get_file_info(self, file_id):
        return {"code": 200, "data": next(item for item in MEETINGS if item["id"] == file_id)}

    def get_conclusion(self, file_id):
        return {"code": 200, "data": {"content": "本次讨论围绕会议回顾与后续跟进展开。\n\n一、让回顾更自然\n会议列表承接搜索，详情集中呈现摘要、转写和录音。保留用户当前的位置，减少来回切换。\n\n二、让讨论有后续\n待办显示状态、截止日期与来源会议，方便回到讨论现场。\n\n三、把决定交给用户\n需要确认时列出具体选项，选择后再明确确认。"}}

    def get_outline(self, file_id):
        return {"code": 200, "data": "00:00 会议目标与背景\n08:20 会议回顾与录音播放\n21:10 待办与后续跟进\n32:40 方案确认与下一步"}

    def get_transcription_text(self, file_id):
        return {"code": 200, "data": "[00:00] 林：今天我们先把会议回顾的体验过一遍。\n\n[00:15] 陈：我希望看完摘要之后，可以直接播放对应的录音。\n\n[00:32] 林：可以，待办也应该能回到它的来源会议。"}

    def get_audio_file(self, file_id):
        return {"code": 200, "data": {"url": f"http://127.0.0.1:{self.port}/sample.wav", "fileName": "示例录音.wav", "mimeType": "audio/wav"}}

    def list_todos(self, status="all", page_num=1, page_size=20):
        items = [item for item in TODOS if status == "all" or bool(item["isCompleted"]) == (status == "done")]
        start = (page_num - 1) * page_size
        return {"code": 200, "data": items[start:start + page_size], "total": len(items)}


def tool_result(name, arguments, port):
    async def run():
        async with Client(create_server(lambda: PreviewAPI(port), lambda: {"configured": True})) as client:
            result = await client.call_tool(name, arguments)
            return result.model_dump(by_alias=True, exclude_none=True)
    return asyncio.run(run())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def respond(self, content, mime="application/json", status=200):
        if not isinstance(content, bytes):
            content = json.dumps(content, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        url = urlsplit(self.path)
        files = {"/": (ROOT / "web/preview/index.html", "text/html; charset=utf-8"),
                 "/app": (APP_HTML, "text/html; charset=utf-8"),
                 "/host.js": (ROOT / "web/preview/host.js", "text/javascript; charset=utf-8")}
        if url.path in files:
            path, mime = files[url.path]
            self.respond(path.read_bytes(), mime)
        elif url.path == "/api/initial":
            view = parse_qs(url.query).get("view", ["meetings"])[0]
            if view == "confirmation":
                result = tool_result("show_confirmation", CONFIRMATION, self.server.server_port)
            else:
                result = tool_result("show_todos" if view == "todos" else "show_meetings", {}, self.server.server_port)
            self.respond(result)
        elif url.path == "/sample.wav":
            stream = io.BytesIO()
            with wave.open(stream, "wb") as audio:
                audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000)
                audio.writeframes(b"\x00\x00" * 8000 * 10)
            self.respond(stream.getvalue(), "audio/wav")
        else:
            self.respond({"error": "Not found"}, status=404)

    def do_POST(self):
        origin = self.headers.get("Origin")
        if self.path != "/api/tool" or origin not in (None, "null", f"http://127.0.0.1:{self.server.server_port}"):
            self.respond({"error": "Forbidden"}, status=403); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 100_000:
                raise ValueError("Invalid request length")
            payload = json.loads(self.rfile.read(length))
            if payload.get("name") not in {"read_meetings_view", "read_meeting_view", "read_todos_view", "get_meeting_audio"}:
                raise ValueError("Only fixture reads are allowed")
            self.respond(tool_result(payload["name"], payload.get("arguments", {}), self.server.server_port))
        except (ValueError, KeyError) as error:
            self.respond({"error": str(error)}, status=400)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Lynse MCP Apps preview (example data): http://127.0.0.1:{server.server_port}", flush=True)
    server.serve_forever()
