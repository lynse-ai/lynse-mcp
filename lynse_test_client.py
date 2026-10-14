"""Optional public test client; checks OAuth/MCP without reading business data."""
from __future__ import annotations

import asyncio
import base64
import hashlib
from html import escape
import secrets
import time
from urllib.parse import urlencode, urlsplit

import requests
from mcp.shared.auth import OAuthClientInformationFull
from starlette.responses import HTMLResponse, RedirectResponse

from lynse_account import AccountError
from lynse_oauth_store import fingerprint

PATH = "/platform/oauth/test"
COOKIE = "lynse_mcp_browser"


def page(title, content, status=200):
    return HTMLResponse(f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex,nofollow">
        <title>{escape(title)}</title><style>body{{font:16px/1.8 system-ui;background:#eef5fa;color:#222;padding:24px}}
        main{{max-width:560px;margin:48px auto;background:white;border-radius:24px;padding:32px}}
        a{{display:inline-block;background:#111;color:white;padding:10px 24px;border-radius:12px;text-decoration:none}}
        code{{overflow-wrap:anywhere}}h1{{font-size:26px}}</style></head>
        <body><main><h1>{escape(title)}</h1>{content}</main></body></html>''', status_code=status,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"})


class TestOAuthClient:
    def __init__(self, provider):
        self.provider = provider
        self.settings = provider.settings
        self.store = provider.store
        self.client_id = "lynse-test-" + fingerprint(self.settings.resource)[:16]
        self.callback = self.settings.web_origin + PATH + "/callback"

    async def handle(self, request):
        action = request.url.path.removeprefix(PATH)
        if action in {"", "/"}:
            return page("Lynse MCP 授权测试", f'''<p>账号服务：<code>{escape(self.settings.api_origin)}</code></p>
                <p>MCP：<code>{escape(self.settings.resource)}</code></p>
                <p>使用测试环境账号登录，授权后验证连接。本次只申请读取权限，
                自动检查仅包含授权码兑换、MCP 初始化和工具发现。</p>
                <a href="{PATH}/start">开始授权测试</a>''')
        try:
            if action == "/start":
                if await self.provider.get_client(self.client_id) is None:
                    await self.provider.register_client(OAuthClientInformationFull(
                        client_id=self.client_id, client_name="Lynse 官网测试客户端", redirect_uris=[self.callback],
                        token_endpoint_auth_method="none", scope="lynse:read",
                        grant_types=["authorization_code", "refresh_token"]))
                state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
                cookie = request.cookies.get(COOKIE) or secrets.token_urlsafe(32)
                self.store.put("test-flow", fingerprint(state), {
                    "verifier": verifier, "browser": fingerprint(cookie)}, time.time() + 600)
                challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
                params = {"client_id": self.client_id, "redirect_uri": self.callback, "response_type": "code",
                          "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
                          "scope": "lynse:read", "resource": self.settings.resource}
                response = RedirectResponse(self.provider.issuer + "authorize?" + urlencode(params), status_code=302)
                response.set_cookie(COOKIE, cookie, httponly=True, secure=self.settings.web_origin.startswith("https:"),
                                    samesite="lax", path="/platform/oauth", max_age=3600)
                return response
            if action == "/callback":
                state = request.query_params.get("state", "")
                with self.store.transaction() as db:
                    flow = self.store.read(db, "test-flow", fingerprint(state))
                    if not flow or flow["browser"] != fingerprint(request.cookies.get(COOKIE, "")) \
                            or request.query_params.get("iss") != self.provider.issuer:
                        raise AccountError("回调无效或已过期，请重新开始。")
                    self.store.delete(db, "test-flow", fingerprint(state))
                if request.query_params.get("error"):
                    return page("授权已取消", f'<a href="{PATH}">重新开始</a>')
                code = request.query_params.get("code", "")
                if not code:
                    raise AccountError("回调缺少授权码，请重新开始。")
                count, scope = await asyncio.to_thread(self._finish, code, flow["verifier"])
                return page("授权测试成功", f'''<p>授权码兑换、MCP 初始化和工具发现均已通过，共 {count} 个工具。</p>
                    <p>权限：<code>{escape(scope)}</code></p>
                    <p>此次临时授权已撤销。未自动读取会议或修改业务数据。</p><a href="{PATH}">再次测试</a>''')
            return page("页面不存在", f'<a href="{PATH}">返回测试入口</a>', 404)
        except (AccountError, ValueError, KeyError, TypeError, requests.RequestException):
            return page("测试连接未完成", f'<p>请重新开始，或查看账号授权页的提示。</p><a href="{PATH}">重新开始</a>', 400)

    def _request(self, method, path, **kwargs):
        # Use the local server, replacing the Host with its validated public authority.
        headers = kwargs.pop("headers", {}) | {"Host": urlsplit(self.settings.resource).netloc}
        with requests.Session() as http:
            http.trust_env = False
            response = http.request(method, "http://127.0.0.1:8000" + path, headers=headers,
                                    timeout=15, allow_redirects=False, **kwargs)
        response.raise_for_status()
        return response

    def _finish(self, code, verifier):
        tokens = self._request("POST", "/token", data={"client_id": self.client_id,
            "grant_type": "authorization_code", "code": code, "code_verifier": verifier,
            "redirect_uri": self.callback, "resource": self.settings.resource}).json()
        session = None
        headers = {"Authorization": "Bearer " + tokens["access_token"],
                   "Accept": "application/json, text/event-stream"}
        try:
            init = self._request("POST", "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1,
                "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": "lynse-website-test", "version": "1"}}})
            session = init.headers["Mcp-Session-Id"]
            headers |= {"Mcp-Session-Id": session, "MCP-Protocol-Version": "2025-11-25"}
            self._request("POST", "/mcp", headers=headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"})
            result = self._request("POST", "/mcp", headers=headers,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).json()
            return len(result["result"]["tools"]), tokens.get("scope", "")
        finally:
            try:
                if session:
                    self._request("DELETE", "/mcp", headers=headers)
            finally:
                self._request("POST", "/revoke", data={"client_id": self.client_id, "token": tokens["refresh_token"]})
