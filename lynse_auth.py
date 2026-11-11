"""Secure browser-based authorization for the Lynse MCP server."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
from urllib.parse import parse_qs, urlparse

from lynse import LynseAPI, LynseAPIError


DEFAULT_API_HOST = "https://gapp.lynse.cn"


def _default_api_host() -> str:
    """Gateway pre-filled on the authorize form; LYNSE_API_HOST overrides it."""
    return os.environ.get("LYNSE_API_HOST") or DEFAULT_API_HOST
MAX_FORM_BYTES = 16_384


def auth_status() -> dict[str, object]:
    """Return credential status without returning any secret values."""
    config_path = Path.home() / ".lynse" / "config.json"
    config: dict[str, object] = {}
    if config_path.is_file():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                config = loaded
        except (OSError, json.JSONDecodeError):
            pass

    api_host = os.environ.get("LYNSE_API_HOST") or config.get("api_host")
    api_key = os.environ.get("LYNSE_API_KEY") or config.get("api_key")
    access_token = os.environ.get("LYNSE_ACCESS_TOKEN") or config.get("access_token")
    if os.environ.get("LYNSE_API_KEY") or os.environ.get("LYNSE_ACCESS_TOKEN"):
        source = "environment"
    elif api_key or access_token:
        source = "user_config"
    else:
        source = "none"

    return {
        "configured": bool(api_host and (api_key or access_token)),
        "api_host": api_host if isinstance(api_host, str) else None,
        "credential_source": source,
    }


@dataclass
class AuthResult:
    configured: bool
    message: str
    api_host: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _save_credentials(api_host: str, api_key: str, access_token: str) -> None:
    config_dir = Path.home() / ".lynse"
    config_path = config_dir / "config.json"
    config_dir.mkdir(parents=True, exist_ok=True)
    try:
        config_dir.chmod(0o700)
    except OSError:
        pass

    config: dict[str, object] = {}
    if config_path.is_file():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                config = loaded
        except (OSError, json.JSONDecodeError):
            pass

    config.update(
        {
            "api_host": api_host,
            "api_key": api_key,
            "access_token": access_token,
        }
    )
    temp_path = config_path.with_suffix(".json.tmp")
    descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temp_path, config_path)
    try:
        config_path.chmod(0o600)
    except OSError:
        pass


def _authenticate(api_host: str, api_key: str) -> None:
    api = LynseAPI(api_host=api_host, api_key=api_key)
    access_token = api.auth_login(api_key, api_host)
    _save_credentials(api_host, api_key, access_token)


class LocalAuthFlow:
    """Collect a Lynse API key through a one-time loopback browser page."""

    def __init__(self) -> None:
        self.elicitation_id = secrets.token_hex(16)
        self._path = f"/authorize/{secrets.token_urlsafe(32)}"
        self._complete = threading.Event()
        self._result = AuthResult(False, "Authorization was not completed.")
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{port}{self._path}"
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="lynse-mcp-auth",
            daemon=True,
        )

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        flow = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *args: object) -> None:
                return

            def _send_html(self, body: str, status: int = 200) -> None:
                payload = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; style-src 'unsafe-inline'; "
                    "form-action 'self'; frame-ancestors 'none'",
                )
                self.end_headers()
                self.wfile.write(payload)

            def _form(self, error: str = "") -> str:
                error_html = (
                    f'<p class="error">{html.escape(error)}</p>' if error else ""
                )
                return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lynse 授权</title>
<style>
body{{font:16px system-ui;background:#f6f7f9;margin:0;color:#17202a}}
main{{max-width:480px;margin:8vh auto;background:white;padding:32px;border-radius:16px;box-shadow:0 8px 32px #0001}}
label{{display:block;margin:18px 0 6px}}input{{box-sizing:border-box;width:100%;padding:12px;border:1px solid #ccd2d9;border-radius:9px}}
button{{width:100%;margin-top:24px;padding:12px;border:0;border-radius:9px;background:#2563eb;color:white;font-weight:650}}
.note{{color:#5f6b7a;font-size:14px;line-height:1.5}}.error{{color:#b42318;background:#fef3f2;padding:10px;border-radius:8px}}
</style></head><body><main>
<h1>连接灵光记 Lynse</h1>
<p class="note">Key 只会提交到本机 127.0.0.1，并保存到受限权限的 ~/.lynse/config.json；不会进入聊天记录。</p>
{error_html}
<form method="post" action="{flow._path}">
<label for="api_host">API 地址</label>
<input id="api_host" name="api_host" type="url" value="{_default_api_host()}" required>
<label for="api_key">授权 Key</label>
<input id="api_key" name="api_key" type="password" autocomplete="off" required autofocus>
<button type="submit">验证并授权</button>
</form></main></body></html>"""

            def do_GET(self) -> None:
                if self.path != flow._path:
                    self._send_html("<h1>Not found</h1>", 404)
                    return
                self._send_html(self._form())

            def do_POST(self) -> None:
                if self.path != flow._path:
                    self._send_html("<h1>Not found</h1>", 404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if length < 1 or length > MAX_FORM_BYTES:
                    self._send_html(self._form("提交内容无效。"), 400)
                    return

                form = parse_qs(self.rfile.read(length).decode("utf-8"))
                api_host = form.get("api_host", [""])[0].strip().rstrip("/")
                api_key = form.get("api_key", [""])[0].strip()
                parsed = urlparse(api_host)
                secure_host = parsed.scheme == "https" and bool(parsed.netloc)
                loopback_http = parsed.scheme == "http" and parsed.hostname in {
                    "127.0.0.1",
                    "localhost",
                }
                if not api_key:
                    self._send_html(self._form("请输入授权 Key。"), 400)
                    return
                if not (secure_host or loopback_http):
                    self._send_html(self._form("API 地址必须使用 HTTPS。"), 400)
                    return

                try:
                    _authenticate(api_host, api_key)
                except (LynseAPIError, OSError, ValueError) as exc:
                    self._send_html(self._form(f"验证失败：{exc}"), 401)
                    return

                flow._result = AuthResult(
                    True, "Lynse authorization completed.", api_host
                )
                flow._complete.set()
                self._send_html(
                    """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>授权成功</title><body style="font:18px system-ui;max-width:520px;margin:12vh auto">
<h1>授权成功</h1><p>现在可以关闭此页面并返回 Codex。</p></body></html>"""
                )

        return Handler

    def start(self) -> str:
        self._thread.start()
        return self.url

    def wait(self, timeout_seconds: float = 300) -> AuthResult:
        if not self._complete.wait(timeout_seconds):
            self._result = AuthResult(False, "Authorization timed out.")
        self.stop()
        return self._result

    def stop(self) -> None:
        # shutdown() waits for serve_forever to exit; skip it when the server
        # thread was never started, or it would block forever.
        if self._thread.is_alive():
            self._server.shutdown()
        self._server.server_close()
