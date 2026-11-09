"""Optional public test client; checks OAuth/MCP without reading business data."""
from __future__ import annotations

import asyncio
import base64
import hashlib
from html import escape
import secrets
import time
from urllib.parse import quote, urlencode, urlsplit

import requests
from mcp.shared.auth import OAuthClientInformationFull
from starlette.responses import HTMLResponse, RedirectResponse

from lynse_account import AccountError
from lynse_oauth_store import fingerprint

PATH = "/platform/oauth/test"
COOKIE = "lynse_mcp_browser"
STATUS_TEXT = {"valid": "有效", "expired": "已过期", "revoked": "已吊销"}
CONSOLE_ACTIONS = {"sms": "sms", "login": "phone-login", "logout": "logout",
                   "revoke": "authorization-revoke", "delete": "authorization-delete"}


def page(title, content, status=200):
    return HTMLResponse(f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex,nofollow">
        <title>{escape(title)}</title><style>body{{font:16px/1.8 system-ui;background:#eef5fa;color:#222;padding:24px}}
        main{{max-width:720px;margin:48px auto;background:white;border-radius:24px;padding:32px}}
        a{{display:inline-block;background:#111;color:white;padding:10px 24px;border-radius:12px;text-decoration:none}}
        code{{overflow-wrap:anywhere}}h1{{font-size:26px}}
        table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:8px;border-bottom:1px solid #eee;text-align:left;vertical-align:top}}
        small{{color:#888}}form.inline{{display:inline}}input{{font:inherit;padding:6px;border:1px solid #ccc;border-radius:8px}}
        button{{font:inherit;padding:6px 14px;border:0;border-radius:8px;background:#111;color:white;cursor:pointer}}
        .notice{{padding:10px 14px;background:#f4f9f4;border-radius:10px;margin:12px 0}}</style></head>
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
                <p><a href="{PATH}/start">开始授权测试</a></p>
                <p><a href="{PATH}/authorizations">我的授权（应用授权记录）</a></p>''')
        try:
            if action == "/authorizations":
                return await self._authorizations_page(request)
            if action.startswith("/authorizations/"):
                return await self._authorizations_submit(request, action.removeprefix("/authorizations/"))
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
                                    samesite="lax", path="/platform/oauth", max_age=86400)
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

    def _api(self, method, action, request, form=None, csrf=""):
        """Call the real website bridge API carrying the browser's own cookie."""
        headers = {"Host": urlsplit(self.settings.web_origin).netloc, "Origin": self.settings.web_origin,
                   "Cookie": request.headers.get("cookie", "")}
        if csrf:
            headers["X-MCP-CSRF"] = csrf
        with requests.Session() as http:
            http.trust_env = False
            return http.request(method, "http://127.0.0.1:8000/platform/oauth/api/" + action,
                                headers=headers, json=form, timeout=15, allow_redirects=False)

    @staticmethod
    def _clock(value):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(value)) if value else "-"

    async def _authorizations_page(self, request):
        api = await asyncio.to_thread(self._api, "GET", "authorizations", request)
        data = api.json() if api.status_code == 200 else {}
        if api.status_code != 200:
            raise AccountError(data.get("error", "授权服务暂时不可用，请稍后重试。"))
        notice = escape(request.query_params.get("msg", "")[:120])
        parts = [f'<p class="notice">{notice}</p>'] if notice else []
        guard = f'<input type="hidden" name="csrf" value="{escape(data["csrf"])}">'
        if data.get("user"):
            parts.append(f'<p>当前账号：<strong>{escape(data["user"]["name"])}</strong> '
                         f'<form class="inline" method="post" action="{PATH}/authorizations/logout">{guard}'
                         f'<button>退出登录</button></form></p>')
            rows = []
            for item in data.get("authorizations", []):
                status = item.get("status", "valid")
                operation = ("revoke", "撤销授权") if status == "valid" else ("delete", "删除")
                rows.append(
                    f'<tr><td>{escape(item["client_name"])}<br><small>{escape(item["client_id"])}</small></td>'
                    f'<td><small>{escape("、".join(item["scopes"]))}</small></td>'
                    f'<td>{STATUS_TEXT.get(status, status)}</td>'
                    f'<td>{self._clock(item["authorized_at"])}</td><td>{self._clock(item["expires_at"])}</td>'
                    f'<td><form class="inline" method="post" action="{PATH}/authorizations/{operation[0]}">{guard}'
                    f'<input type="hidden" name="authorization_id" value="{escape(item["authorization_id"])}">'
                    f'<button>{operation[1]}</button></form></td></tr>')
            parts.append('<table><tr><th>应用</th><th>权限</th><th>状态</th><th>授权时间</th>'
                         f'<th>到期时间</th><th>操作</th></tr>{"".join(rows)}</table>' if rows
                         else "<p>你还没有授权任何第三方应用。当你授权应用访问账号后，会在这里展示。</p>")
        else:
            parts.append(f'''<p>登录后可查看并管理已授权的 MCP 应用连接。</p>
                <form method="post" action="{PATH}/authorizations/sms">{guard}
                <label>手机号 <input name="phone" inputmode="numeric" pattern="1[3-9]\\d{9}" required></label>
                <button>发送验证码</button></form>
                <form method="post" action="{PATH}/authorizations/login">{guard}
                <label>手机号 <input name="phone" inputmode="numeric" pattern="1[3-9]\\d{9}" required></label>
                <label>验证码 <input name="code" inputmode="numeric" required></label>
                <button>登录</button></form>''')
        response = page("我的授权", "".join(parts))
        if api.headers.get("Set-Cookie"):
            response.headers["Set-Cookie"] = api.headers["Set-Cookie"]
        return response

    async def _authorizations_submit(self, request, op):
        action = CONSOLE_ACTIONS.get(op)
        if not action:
            return page("页面不存在", f'<a href="{PATH}/authorizations">返回我的授权</a>', 404)
        form = await request.form()
        api_form = {"csrf": str(form.get("csrf", ""))}
        if op in {"sms", "login"}:
            api_form |= {"phone": str(form.get("phone", ""))}
            if op == "login":
                api_form["code"] = str(form.get("code", ""))
        else:
            api_form["authorization_id"] = str(form.get("authorization_id", ""))
        api = await asyncio.to_thread(
            self._api, "POST", action, request, form=api_form, csrf=api_form["csrf"])
        data = api.json() if api.headers.get("content-type", "").startswith("application/json") else {}
        if api.status_code != 200:
            message = data.get("error", "操作失败，请重试。")
        else:
            message = {"sms": "验证码已发送。", "phone-login": "登录成功。", "logout": "已退出登录。",
                       "authorization-revoke": "已撤销该应用的授权。",
                       "authorization-delete": "已删除该授权记录。"}[action]
        response = RedirectResponse(PATH + "/authorizations?msg=" + quote(message), status_code=303)
        if api.headers.get("Set-Cookie"):
            response.headers["Set-Cookie"] = api.headers["Set-Cookie"]
        return response

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
