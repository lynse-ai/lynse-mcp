"""Loopback-only OAuth client for manually debugging the website login flow.

Run beside lynse_remote.py and Vite. Credentials stay in this process, not HTML/logs.
"""
from __future__ import annotations

import base64
import hashlib
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

RESOURCE = os.environ.get("LYNSE_MCP_PUBLIC_URL", "http://127.0.0.1:8000/mcp")
if urlsplit(RESOURCE).scheme != "http" or urlsplit(RESOURCE).hostname not in {"localhost", "127.0.0.1"}:
    raise ValueError("The debug client requires a loopback HTTP MCP URL")
ORIGIN = RESOURCE.removesuffix("/mcp")
PORT = 8765
CALLBACK = f"http://127.0.0.1:{PORT}/callback"
flows: dict[str, dict] = {}
client: dict | None = None
http = requests.Session()
http.trust_env = False  # A system proxy must not intercept local OAuth credentials.


def request(method, path, **kwargs):
    response = http.request(method, ORIGIN + path, timeout=15, allow_redirects=False, **kwargs)
    response.raise_for_status()
    return response


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Callback query strings contain authorization codes.

    def page(self, title, content, status=200):
        document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{escape(title)}</title><style>
        body{{font:16px/1.7 system-ui;background:#eff5fa;color:#222;padding:24px}}
        main{{max-width:600px;margin:40px auto;background:white;border-radius:20px;padding:32px}}
        a,button{{display:inline-block;background:#111;color:white;padding:10px 20px;
        border:0;border-radius:10px;text-decoration:none;font:inherit}}code{{overflow-wrap:anywhere}}
        </style><main><h1>{escape(title)}</h1>{content}</main></html>'''.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(document)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(document)

    def do_GET(self):
        global client
        if self.headers.get("Host") not in {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}:
            return self.page("访问地址无效", "请使用本机调试地址。", 400)
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        now = time.time()
        for key in list(flows):
            if flows[key]["expires"] <= now:
                del flows[key]
        try:
            if parsed.path == "/":
                backend = escape(os.environ.get("LYNSE_ACCOUNT_API_ORIGIN", "https://gapp.lynse.cn"))
                return self.page("Lynse MCP 本地调试", f'''<p>账号后端：<code>{backend}</code></p>
                    <p>MCP：<code>{escape(RESOURCE)}</code></p>
                    <p>点击后进入官网登录页，登录并允许访问后会返回此处验证授权。</p>
                    <form action="/connect"><p><label><input type="checkbox" name="write" value="1">
                    同时申请写权限（默认只读）</label></p><button>开始授权调试</button></form>''')
            if parsed.path == "/connect":
                if len(flows) >= 50:
                    return self.page("调试请求过多", "请稍后重试。", 429)
                if client is None:
                    client = request("POST", "/register", json={
                        "client_name": "Lynse 本地调试客户端", "redirect_uris": [CALLBACK],
                        "token_endpoint_auth_method": "none", "scope": "lynse:read lynse:write",
                        "grant_types": ["authorization_code", "refresh_token"],
                    }).json()
                state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
                challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
                flows[state] = {"verifier": verifier, "expires": now + 600}
                response = request("GET", "/authorize", params={
                    "client_id": client["client_id"], "redirect_uri": CALLBACK, "response_type": "code",
                    "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
                    "scope": "lynse:read lynse:write" if query.get("write") == ["1"] else "lynse:read",
                    "resource": RESOURCE,
                })
                if response.status_code != 302:
                    return self.page("无法发起授权", '<p>请确认 MCP 服务配置。</p><a href="/">重新开始</a>', 502)
                self.send_response(302)
                self.send_header("Location", response.headers["Location"])
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if parsed.path == "/callback":
                flow = flows.pop((query.get("state") or [""])[0], None)
                if not flow or query.get("iss") != [ORIGIN + "/"]:
                    return self.page("回调无效或已过期", '<a href="/">重新开始</a>', 400)
                if query.get("error"):
                    return self.page("授权已取消", '<a href="/">重新开始</a>')
                code = (query.get("code") or [""])[0]
                tokens = request("POST", "/token", data={
                    "client_id": client["client_id"], "grant_type": "authorization_code", "code": code,
                    "redirect_uri": CALLBACK, "code_verifier": flow["verifier"], "resource": RESOURCE,
                }).json()
                headers = {"Authorization": "Bearer " + tokens["access_token"],
                           "Accept": "application/json, text/event-stream"}
                init = request("POST", "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1,
                    "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                    "clientInfo": {"name": "lynse-local-debug", "version": "1"}}})
                session = init.headers.get("Mcp-Session-Id")
                headers |= {"Mcp-Session-Id": session, "MCP-Protocol-Version": "2025-11-25"}
                tools = request("POST", "/mcp", headers=headers,
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).json()
                count = len(tools["result"]["tools"])
                request("DELETE", "/mcp", headers=headers)
                request("POST", "/revoke", data={"client_id": client["client_id"],
                    "token": tokens["refresh_token"]})
                return self.page("授权成功", f'''<p>授权码兑换、MCP 初始化与工具发现均已通过，共 {count} 个工具。</p>
                    <p>权限：<code>{escape(tokens.get('scope', ''))}</code></p>
                    <p>调试完成，本次临时授权已撤销。此页面不会显示 Token，也不会自动读取会议、修改业务数据。</p>
                    <a href="/">再次调试</a>''')
            return self.page("页面不存在", '<a href="/">返回调试入口</a>', 404)
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return self.page("调试连接未完成", '<p>请确认 MCP 服务正在运行，或在官网授权页检查账号提示。</p><a href="/">重新开始</a>', 502)


if __name__ == "__main__":
    print(f"Local OAuth debugger: http://127.0.0.1:{PORT}", flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
