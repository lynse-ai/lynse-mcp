"""Account-authenticated Streamable HTTP MCP and website OAuth bridge."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import os
import re
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.middleware.client_auth import AuthenticationError, ClientAuthenticator
from mcp.server.auth.handlers.revoke import RevocationRequest
from mcp.server.auth.routes import cors_middleware
from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
    RefreshToken, RegistrationError, TokenError, construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.responses import Response
from starlette.routing import Route
from pydantic import ValidationError
from lynse import LynseAPIError

from lynse_account import AccountBackend, AccountError, AccountLynseAPI
from lynse_mcp import LynseMCPTools, create_server
from lynse_oauth_store import OAuthStore, fingerprint

READ = "lynse:read"
WRITE = "lynse:write"
COOKIE = "lynse_mcp_browser"
API_PATH = "/platform/oauth/api"


def secure_origin(value: str) -> str:
    p = urlsplit(value)
    if (p.scheme != "https" and not (p.scheme == "http" and p.hostname in {"localhost", "127.0.0.1"})) \
            or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError("Public URLs must use HTTPS (HTTP is allowed only on loopback)")
    return f"{p.scheme}://{p.netloc}"


@dataclass(frozen=True)
class Settings:
    resource: str
    web_origin: str
    api_origin: str
    encryption_key: str
    database: str
    wechat_callback: str = ""
    wechat_type: str = "WECHAT_JUMP"
    redirect_schemes: tuple[str, ...] = ("workbuddy", "cursor", "vscode")
    test_ui: bool = False

    def __post_init__(self):
        secure_origin(self.resource)
        if urlsplit(self.resource).path != "/mcp":
            raise ValueError("MCP public URL must use the /mcp path")
        for origin in (self.web_origin, self.api_origin):
            if secure_origin(origin) != origin:
                raise ValueError("Website/API origin must have no path or trailing slash")
        if self.wechat_callback and self.wechat_callback != self.web_origin + "/platform/oauth/wechat":
            raise ValueError("WeChat callback must be the website's /platform/oauth/wechat page")

    @classmethod
    def from_env(cls):
        return cls(
            resource=os.environ.get("LYNSE_MCP_PUBLIC_URL", "https://mcp.lynse.cn/mcp"),
            web_origin=os.environ.get("LYNSE_WEB_ORIGIN", "https://www.lynse.cn"),
            api_origin=os.environ.get("LYNSE_ACCOUNT_API_ORIGIN", "https://api.lynse.cn"),
            encryption_key=os.environ["LYNSE_MCP_ENCRYPTION_KEY"],
            database=os.environ.get("LYNSE_MCP_DATABASE", "runtime-data/oauth.sqlite3"),
            wechat_callback=os.environ.get("LYNSE_WECHAT_CALLBACK_URL", ""),
            wechat_type=os.environ.get("LYNSE_WECHAT_AUTH_TYPE", "WECHAT_JUMP"),
            redirect_schemes=tuple(os.environ.get(
                "LYNSE_MCP_REDIRECT_SCHEMES", "workbuddy,cursor,vscode").split(",")),
            test_ui=os.environ.get("LYNSE_MCP_TEST_UI", "0") == "1",
        )


class GrantCode(AuthorizationCode):
    grant_id: str


class GrantRefresh(RefreshToken):
    grant_id: str


class AccountOAuthProvider:
    def __init__(self, settings: Settings, store: OAuthStore, backend: AccountBackend):
        self.settings, self.store, self.backend = settings, store, backend
        # Match the SDK's AnyHttpUrl canonical representation in both discovery documents.
        self.issuer = secure_origin(settings.resource) + "/"

    async def get_client(self, client_id: str):
        value = self.store.get("client", client_id)
        return OAuthClientInformationFull.model_validate(value) if value else None

    async def register_client(self, client: OAuthClientInformationFull):
        if not client.redirect_uris or len(client.redirect_uris) > 10:
            raise RegistrationError("invalid_redirect_uri", "Register 1 to 10 redirect URIs")
        for uri in client.redirect_uris:
            p = urlsplit(str(uri))
            allowed = (p.scheme == "https" and bool(p.hostname)) or (
                p.scheme == "http" and p.hostname in {"localhost", "127.0.0.1", "::1"}
            ) or p.scheme in self.settings.redirect_schemes
            if not allowed or p.fragment or p.username or p.password:
                raise RegistrationError("invalid_redirect_uri", "Redirect URI is not permitted")
        self.store.put("client", client.client_id, client.model_dump(mode="json"), time.time() + 90 * 86400)

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams):
        if params.resource != self.settings.resource:
            raise AuthorizeError("invalid_target",
                                 f"resource is required and must equal {self.settings.resource}")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.code_challenge):
            raise AuthorizeError("invalid_request", "S256 PKCE challenge is required")
        scopes = params.scopes or [READ]
        if READ not in scopes or not set(scopes).issubset({READ, WRITE}):
            raise AuthorizeError("invalid_scope", "Unsupported Lynse scopes")
        ticket = secrets.token_urlsafe(32)
        self.store.put("pending", fingerprint(ticket), {
            "client_id": client.client_id, "client_name": (client.client_name or "MCP 客户端")[:120],
            "params": params.model_dump(mode="json") | {"scopes": scopes},
            "csrf": secrets.token_urlsafe(32), "browser": None, "expires": time.time() + 600,
        }, time.time() + 600)
        return self.settings.web_origin + "/platform/oauth?" + urlencode({"ticket": ticket})

    async def load_authorization_code(self, client, code):
        value = self.store.get("code", fingerprint(code))
        if not value or value["client_id"] != client.client_id:
            return None
        return GrantCode(code=code, **value)

    def _issue(self, db, client_id, grant_id, scopes):
        grant = self.store.read(db, "grant", grant_id)
        if not grant or not set(scopes).issubset(grant["scopes"]):
            raise TokenError("invalid_grant", "Authorization expired; sign in again")
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        lifetime = min(900, int(grant["expires"] - time.time()))
        if lifetime <= 0:
            raise TokenError("invalid_grant", "Authorization expired")
        common = {"client_id": client_id, "grant_id": grant_id, "scopes": scopes,
                  "subject": grant["subject"], "resource": self.settings.resource}
        self.store.write(db, "access", fingerprint(access), common | {"expires_at": int(time.time()) + lifetime},
                         time.time() + lifetime)
        self.store.write(db, "refresh", fingerprint(refresh), common | {"expires_at": int(grant["expires"])},
                         grant["expires"])
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=lifetime,
                          refresh_token=refresh, scope=" ".join(scopes))

    async def exchange_authorization_code(self, client, authorization_code):
        key = fingerprint(authorization_code.code)
        with self.store.transaction() as db:
            value = self.store.read(db, "code", key)
            if not value or value["client_id"] != client.client_id:
                raise TokenError("invalid_grant", "Authorization code already used or expired")
            self.store.delete(db, "code", key)
            return self._issue(db, client.client_id, value["grant_id"], value["scopes"])

    async def load_refresh_token(self, client, refresh_token):
        value = self.store.get("refresh", fingerprint(refresh_token))
        if not value or value["client_id"] != client.client_id:
            return None
        return GrantRefresh(token=refresh_token, **value)

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        with self.store.transaction() as db:
            key = fingerprint(refresh_token.token)
            value = self.store.read(db, "refresh", key)
            if not value or value["client_id"] != client.client_id or not set(scopes).issubset(value["scopes"]):
                raise TokenError("invalid_grant", "Refresh token already used or expired")
            self.store.delete(db, "refresh", key)
            return self._issue(db, client.client_id, value["grant_id"], scopes)

    async def verify_token(self, token: str):
        value = self.store.get("access", fingerprint(token))
        if not value or not self.store.get("grant", value["grant_id"]):
            return None
        return AccessToken(token=token, client_id=value["client_id"], scopes=value["scopes"],
                           expires_at=value["expires_at"], resource=self.settings.resource,
                           subject=value["subject"], claims={"iss": self.issuer, "grant_id": value["grant_id"]})

    async def load_access_token(self, token: str):
        return await self.verify_token(token)

    async def revoke_token(self, token):
        with self.store.transaction() as db:
            for kind in ("access", "refresh"):
                value = self.store.read(db, kind, fingerprint(token.token))
                if value:
                    self.store.delete(db, "grant", value["grant_id"])
                    self.store.delete(db, kind, fingerprint(token.token))

    def principal(self):
        access = get_access_token()
        if not access or not access.subject or READ not in access.scopes:
            raise ValueError("Connect through the website OAuth flow first")
        grant = self.store.get("grant", (access.claims or {}).get("grant_id", ""))
        if not grant or grant["subject"] != access.subject:
            raise ValueError("Authorization expired or revoked; reconnect")
        return access, grant


class AccountTools(LynseMCPTools):
    def __init__(self, provider: AccountOAuthProvider):
        self.provider = provider

    @property
    def api(self):
        access, grant = self.provider.principal()
        return AccountLynseAPI(self.provider.settings.api_origin, grant["upstream_token"], access.subject)

    def get_auth_status(self):
        access, _ = self.provider.principal()
        return {"configured": True, "credential_source": "account_oauth", "scopes": access.scopes}

    async def _ensure_authorized(self, ctx):
        self.provider.principal()

    @staticmethod
    async def _call(operation, *args, **kwargs):
        try:
            return await asyncio.to_thread(operation, *args, **kwargs)
        except LynseAPIError as exc:
            # Upstream error bodies can contain credentials/internal diagnostics.
            if exc.http_code in (401, 403):
                raise ValueError("Lynse access denied or expired; reconnect through the website.") from None
            raise ValueError("Lynse request failed; please try again later.") from None

    def _confirmed(self, confirm: bool, action: str):
        access, _ = self.provider.principal()
        if WRITE not in access.scopes:
            raise ValueError("This connection has read permission only; authorize lynse:write to make changes")
        super()._confirmed(confirm, action)


class WebsiteBridge:
    def __init__(self, provider):
        self.provider = provider
        self.store, self.backend, self.settings = provider.store, provider.backend, provider.settings

    async def handle(self, request):
        try:
            body = await request.json() if request.method == "POST" else {}
            if not isinstance(body, dict):
                raise AccountError("授权请求无效，请重新连接。")
            ticket = body.get("ticket") or request.query_params.get("ticket", "")
            if not isinstance(ticket, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", ticket):
                raise AccountError("授权链接无效，请从 MCP 客户端重新连接。")
            cookie = request.cookies.get(COOKIE) or secrets.token_urlsafe(32)
            browser_key = fingerprint(cookie)
            key = fingerprint(ticket)
            with self.store.transaction() as db:
                pending = self.store.read(db, "pending", key)
                if not pending:
                    raise AccountError("授权链接已过期，请从 MCP 客户端重新连接。")
                if pending["browser"] not in (None, browser_key):
                    raise AccountError("请在最初打开授权链接的浏览器中继续。")
                if request.method == "POST":
                    if (pending["browser"] is None or request.headers.get("origin") != self.settings.web_origin
                            or not secrets.compare_digest(request.headers.get("x-mcp-csrf", ""), pending["csrf"])):
                        return JSONResponse({"error": "授权请求校验失败，请重新打开页面。"}, status_code=403)
                else:
                    pending["browser"] = browser_key
                    self.store.write(db, "pending", key, pending, pending["expires"])
            action = request.path_params["action"]
            if action == "context" and request.method == "GET":
                session = self.store.get("browser", browser_key)
                response = JSONResponse({"client_name": pending["client_name"], "scopes": pending["params"]["scopes"],
                                         "csrf": pending["csrf"], "user": {"name": session["name"]} if session else None,
                                         "wechat_enabled": bool(self.settings.wechat_callback)})
                response.set_cookie(COOKIE, cookie, httponly=True, secure=self.settings.web_origin.startswith("https:"),
                                    samesite="lax", path="/platform/oauth", max_age=3600)
                return response
            if request.method != "POST":
                return JSONResponse({"error": "Method not allowed"}, status_code=405)
            peer = request.client.host if request.client else "unknown"
            if self.store.rate_limit("website:" + peer + ":" + action, 20, 600):
                return JSONResponse({"error": "操作过于频繁，请稍后重试。"}, status_code=429)
            if action in {"sms", "phone-login"}:
                phone = body.get("phone", "")
                if not isinstance(phone, str) or not re.fullmatch(r"1[3-9]\d{9}", phone):
                    raise AccountError("请输入有效的中国大陆手机号。")
                if action == "sms":
                    if self.store.rate_limit("sms:" + fingerprint(phone), 1, 60):
                        return JSONResponse({"error": "请稍后再发送验证码。"}, status_code=429)
                    return JSONResponse(await asyncio.to_thread(self.backend.sms, phone))
                code = body.get("code", "")
                if not isinstance(code, str) or not re.fullmatch(r"(?:\d{4}|\d{6})", code):
                    raise AccountError("请输入 4 位或 6 位验证码。")
                session = await asyncio.to_thread(self.backend.phone_login, phone, code)
                self._save_session(browser_key, session)
                return JSONResponse({"user": {"name": session["name"]}})
            if action == "logout":
                with self.store.transaction() as db:
                    self.store.delete(db, "browser", browser_key)
                return JSONResponse({"ok": True})
            if action == "wechat-start":
                if not self.settings.wechat_callback:
                    raise AccountError("微信登录暂未配置，请使用手机验证码登录。")
                url = await asyncio.to_thread(self.backend.wechat_url)
                p = urlsplit(url)
                query = parse_qs(p.query)
                state = (query.get("state") or [""])[0]
                if p.scheme != "https" or p.hostname != "open.weixin.qq.com" or not state:
                    raise AccountError("微信登录配置异常，请使用手机验证码登录。")
                query["redirect_uri"] = [self.settings.wechat_callback]
                self.store.put("wechat", fingerprint(state), {"browser": browser_key, "ticket": key}, time.time() + 300)
                return JSONResponse({"url": urlunsplit((p.scheme, p.netloc, p.path, urlencode(query, doseq=True), p.fragment))})
            if action == "wechat-complete":
                state, code = body.get("state", ""), body.get("code", "")
                if not isinstance(state, str) or not isinstance(code, str) or not code or len(code) > 512:
                    raise AccountError("微信登录回调无效，请重新登录。")
                with self.store.transaction() as db:
                    flow = self.store.read(db, "wechat", fingerprint(state))
                    if not flow or flow["browser"] != browser_key or flow["ticket"] != key:
                        raise AccountError("微信登录状态无效或已过期，请重新登录。")
                    self.store.delete(db, "wechat", fingerprint(state))
                session = await asyncio.to_thread(self.backend.wechat_login, code, state)
                self._save_session(browser_key, session)
                return JSONResponse({"user": {"name": session["name"]}})
            if action == "consent":
                decision = body.get("decision")
                if decision not in {"allow", "deny"}:
                    raise AccountError("请选择允许或拒绝授权。")
                session = self.store.get("browser", browser_key)
                if decision == "allow":
                    if not session:
                        raise AccountError("请先登录你的 Lynse 账号。")
                    user = await asyncio.to_thread(self.backend.current_user, session["upstream_token"])
                    if str(user["id"]) != session["subject"]:
                        raise AccountError("账号身份已变化，请重新登录。")
                with self.store.transaction() as db:
                    pending = self.store.read(db, "pending", key)
                    if not pending or pending["browser"] != browser_key:
                        raise AccountError("授权请求已经处理，请重新连接。")
                    self.store.delete(db, "pending", key)
                    params = pending["params"]
                    redirect_args = {"state": params.get("state"), "iss": self.provider.issuer}
                    if decision == "deny":
                        redirect_args["error"] = "access_denied"
                    else:
                        code, grant_id = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                        expires = session["expires"]
                        grant = {"subject": session["subject"], "upstream_token": session["upstream_token"],
                                 "scopes": params["scopes"], "expires": expires}
                        self.store.write(db, "grant", grant_id, grant, expires)
                        auth_code = {"client_id": pending["client_id"], "grant_id": grant_id,
                                     "scopes": params["scopes"], "expires_at": time.time() + 120,
                                     "subject": session["subject"], "code_challenge": params["code_challenge"],
                                     "redirect_uri": params["redirect_uri"], "resource": params["resource"],
                                     "redirect_uri_provided_explicitly": params["redirect_uri_provided_explicitly"]}
                        self.store.write(db, "code", fingerprint(code), auth_code, time.time() + 120)
                        redirect_args["code"] = code
                return JSONResponse({"redirect_uri": construct_redirect_uri(params["redirect_uri"], **redirect_args)})
            return JSONResponse({"error": "Unknown action"}, status_code=404)
        except (AccountError, ValueError, TypeError):
            # Only deliberate AccountError text is suitable for the browser.
            import sys
            exc = sys.exception()
            message = str(exc) if isinstance(exc, AccountError) else "授权请求无效，请重新连接。"
            return JSONResponse({"error": message}, status_code=400)

    def _save_session(self, key, session):
        session = session | {"expires": time.time() + session["lifetime"]}
        self.store.put("browser", key, session, min(session["expires"], time.time() + 3600))


class OAuthSecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, provider):
        super().__init__(app)
        self.provider = provider

    async def dispatch(self, request, call_next):
        path = request.url.path
        protected = path in {"/register", "/authorize", "/token", "/revoke"} or path.startswith(API_PATH) \
            or path.startswith("/platform/oauth/test")
        if protected:
            body = await request.body()
            if len(body) > 16384:
                return JSONResponse({"error": "invalid_request"}, status_code=413)
            peer = request.client.host if request.client else "unknown"
            maximum = 10 if path == "/register" else 120
            if self.provider.store.rate_limit(path + ":" + peer, maximum, 600):
                return JSONResponse({"error": "slow_down"}, status_code=429)
            if path == "/token" and request.method == "POST":
                form = parse_qs(body.decode("utf-8", errors="replace"))
                if form.get("resource") != [self.provider.settings.resource]:
                    return JSONResponse({"error": "invalid_target", "error_description":
                        f"resource is required and must equal {self.provider.settings.resource}"}, status_code=400)
        response = await call_next(request)
        if path == "/authorize" and "location" in response.headers:
            destination = urlsplit(response.headers["location"])
            query = parse_qs(destination.query, keep_blank_values=True)
            if "error" in query or "code" in query:
                query["iss"] = [self.provider.issuer]
                response.headers["Location"] = urlunsplit((destination.scheme, destination.netloc,
                    destination.path, urlencode(query, doseq=True), destination.fragment))
        if path.endswith("/.well-known/oauth-authorization-server") and request.method == "GET" and response.status_code == 200:
            # The SDK accepts public clients but doesn't list `none` in metadata yet.
            import json
            body = b"".join([chunk async for chunk in response.body_iterator])
            metadata = json.loads(body)
            metadata["token_endpoint_auth_methods_supported"] = ["none", "client_secret_post", "client_secret_basic"]
            metadata["revocation_endpoint_auth_methods_supported"] = ["none", "client_secret_post", "client_secret_basic"]
            metadata["authorization_response_iss_parameter_supported"] = True
            response = JSONResponse(metadata)
            response.headers["Access-Control-Allow-Origin"] = "*"
        if protected:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response


def revocation_handler(provider):
    """Retain SDK client authentication while allowing RFC 7009 public clients.

    SDK 2.0's revocation model requires client_secret even when auth method is none.
    Supplying the missing optional value during validation fixes that incompatibility.
    """
    authenticator = ClientAuthenticator(provider)

    async def handle(request):
        try:
            client = await authenticator.authenticate_request(request)
        except AuthenticationError:
            return JSONResponse({"error": "unauthorized_client"}, status_code=401)
        try:
            form = dict(await request.form())
            form.setdefault("client_secret", None)
            params = RevocationRequest.model_validate(form)
        except (ValidationError, ValueError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        token = await provider.load_access_token(params.token)
        if token is None:
            token = await provider.load_refresh_token(client, params.token)
        if token and token.client_id == client.client_id:
            await provider.revoke_token(token)
        return Response(status_code=200, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    return handle


def create_app(settings: Settings, backend=None):
    store = OAuthStore(settings.database, settings.encryption_key)
    provider = AccountOAuthProvider(settings, store, backend or AccountBackend(settings.api_origin, settings.wechat_type))
    server = create_server(tools=AccountTools(provider), remote=True, auth_server_provider=provider,
                           auth=AuthSettings(
                               issuer_url=provider.issuer, resource_server_url=settings.resource,
                               required_scopes=[READ],
                               client_registration_options=ClientRegistrationOptions(
                                   enabled=True, valid_scopes=[READ, WRITE], default_scopes=[READ]),
                               revocation_options=RevocationOptions(enabled=True)))
    bridge = WebsiteBridge(provider)
    server.custom_route(API_PATH + "/{action}", methods=["GET", "POST"])(bridge.handle)
    if settings.test_ui:
        from lynse_test_client import PATH, TestOAuthClient
        client = TestOAuthClient(provider)
        for path in (PATH, PATH + "/", PATH + "/start", PATH + "/callback"):
            server.custom_route(path, methods=["GET"])(client.handle)
    async def health(request):
        return JSONResponse({"status": "ok"})
    server.custom_route("/health", methods=["GET"])(health)
    host = urlsplit(settings.resource).netloc
    app = server.streamable_http_app(streamable_http_path=urlsplit(settings.resource).path, json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[host, host + ":443", urlsplit(settings.web_origin).netloc, "127.0.0.1:*", "localhost:*"],
            allowed_origins=[settings.web_origin, secure_origin(settings.resource)]))
    app.routes[:] = [Route("/revoke", endpoint=cors_middleware(revocation_handler(provider), ["POST", "OPTIONS"]),
        methods=["POST", "OPTIONS"]) if getattr(route, "path", None) == "/revoke" else route for route in app.routes]
    app.add_middleware(OAuthSecurityMiddleware, provider=provider)
    app.state.oauth_provider = provider
    return app


def main():
    import uvicorn
    parser = argparse.ArgumentParser(description="Run account-authenticated Lynse MCP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(create_app(Settings.from_env()), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
