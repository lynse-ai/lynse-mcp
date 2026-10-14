"""Bridge existing Lynse phone/WeChat login without local credential files."""
from __future__ import annotations

import requests
from lynse import LynseAPI, LynseAPIError


class AccountError(ValueError):
    pass


class AccountBackend:
    def __init__(self, origin: str, wechat_type: str = "WECHAT_JUMP"):
        self.origin = origin.rstrip("/")
        self.wechat_type = wechat_type

    def request(self, method: str, path: str, *, token=None, **kwargs):
        headers = {"Accept": "application/json", "Accept-Language": "zh-CN"}
        if token:
            headers["Authorization"] = token
        try:
            response = requests.request(method, self.origin + path, headers=headers,
                                        timeout=15, allow_redirects=False, **kwargs)
            envelope = response.json()
        except (requests.RequestException, ValueError):
            raise AccountError("账号服务暂时不可用，请稍后重试。") from None
        if not response.ok or not isinstance(envelope, dict) or envelope.get("code") != 200:
            # Upstream messages may contain credentials or internal diagnostics.
            raise AccountError("账号验证未通过，请检查验证码或重新登录。")
        return envelope.get("data")

    def sms(self, phone: str):
        result = self.request("GET", "/api/auth/captcha/sms",
                              params={"phone": phone, "actionType": "LOGIN", "dialCode": "86"})
        if result is not True and not (isinstance(result, dict) and result.get("success")):
            raise AccountError("验证码发送失败，请稍后重试。")
        return {"cooldown": int(result.get("cooldown") or 60) if isinstance(result, dict) else 60}

    def phone_login(self, phone: str, code: str):
        return self._login({"authType": "PHONE_CODE", "username": phone,
                            "captchaCode": code, "dialCode": "86"})

    def wechat_url(self):
        return self.request("GET", "/api/auth/render", params={"authType": self.wechat_type})

    def wechat_login(self, code: str, state: str):
        return self._login({"authType": self.wechat_type, "code": code, "state": state})

    def _login(self, body: dict):
        result = self.request("POST", "/api/auth/login", json=body)
        if not isinstance(result, dict) or not result.get("token") or result.get("isNeedBind"):
            raise AccountError("请先在灵光记 App 完成账号绑定，再返回授权。")
        token = result["token"]
        user = self.current_user(token)
        return {"upstream_token": token, "subject": str(user["id"]),
                "name": str(user.get("nickname") or "Lynse 用户")[:80],
                "lifetime": min(max(int(result.get("expireIn") or 86400), 1), 86400)}

    def current_user(self, token: str):
        user = self.request("GET", "/api/business/customer/current", token=token)
        if not isinstance(user, dict) or not user.get("id"):
            raise AccountError("无法验证当前账号，请重新登录。")
        return user


class AccountLynseAPI(LynseAPI):
    """Each tool call receives an explicit account token; no env/file fallback."""
    def __init__(self, origin: str, token: str, subject: str):
        # Intentionally bypass the CLI constructor's env/config/cache resolution.
        self.api_host = origin
        self.api_key = ""
        self.owner_id = subject
        self._owner_verified_token = None
        self._forced_access_token = token
        self._access_token = token
        self._http = requests

    def _get_token(self, refresh: bool = False):
        if refresh:
            raise LynseAPIError("Lynse login expired; reconnect through the website.", http_code=401)
        return self._forced_access_token

    def _check_owner_id(self, token: str, **_kwargs):
        # Keep this boundary in the remote adapter; older CLI releases lack it.
        if self._owner_verified_token == token:
            return token
        try:
            response = requests.get(self.api_host + "/api/business/customer/current",
                                    headers={"Authorization": token, "Accept": "application/json"},
                                    timeout=10, allow_redirects=False)
            if response.status_code != 200:
                raise LynseAPIError("Unable to verify account owner.", http_code=response.status_code)
            envelope = response.json()
            user = envelope.get("data") if isinstance(envelope, dict) else None
            if not isinstance(envelope, dict) or envelope.get("code") != 200 or not isinstance(user, dict):
                raise LynseAPIError("Unable to verify account owner.")
            if not self.owner_id or not user.get("id") or str(user["id"]) != str(self.owner_id):
                raise LynseAPIError("Account authorization no longer matches this user.", http_code=403)
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            raise LynseAPIError("Unable to verify account owner.") from None
        self._owner_verified_token = token
        return token

    def _request(self, *args, **kwargs):
        self._check_owner_id(self._get_token())
        return super()._request(*args, **kwargs)

    def _resolve_x_api_key(self, token: str):
        return ""

    def _should_use_bearer_authorization(self, token: str):
        return False

    def _save_token(self, token: str):
        raise RuntimeError("Remote account tokens must never be written to CLI cache")
