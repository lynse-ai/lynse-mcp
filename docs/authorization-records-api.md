# 应用授权记录桥接 API（官网「我的授权」控制台）

面向官网前端（`McpOAuthPage.tsx` 同仓库的控制台页面）的契约。所有端点由 MCP 服务提供，经官网 Nginx 同源代理 `/platform/oauth/api/*`，对浏览器而言与授权页同一来源。对应实现见 `lynse_remote.py` 的 `WebsiteBridge._account` 与 `AccountOAuthProvider`；`LYNSE_MCP_TEST_UI=1` 的测试实例在 `/platform/oauth/test/authorizations` 提供等价的自测页面。

## 通用约定

- 浏览器身份：HttpOnly Cookie `lynse_mcp_browser`（`path=/platform/oauth`，`SameSite=Lax`）。首次请求可不带，响应会 `Set-Cookie` 下发，前端无需读写。
- 登录会话：官网手机/微信登录后建立（`sms` → `phone-login`，或微信回调），与授权页共用；登录动作允许无 ticket 从控制台直接发起。
- 所有 POST 要求请求头 `Origin` 等于官网源，且 `X-MCP-CSRF` 等于 `GET authorizations` 下发的 `csrf`（恒定时间比较）。缺失或不匹配返回 403。
- 错误响应统一为 `{"error": "<中文提示>"}`：400（未登录/记录不存在/状态不允许）、403（Origin/CSRF 校验失败）、405（方法不符）、429（限流）。
- 响应不含任何 Token 或上游凭据；不要在日志中记录 Cookie、CSRF 或完整 URL。

## GET /platform/oauth/api/authorizations

列出当前浏览器会话对应账号的授权记录。未登录时 `user` 为 `null`、列表为空，但仍下发 `csrf`（登录表单需要）。

```json
{
  "user": {"name": "张三"},
  "csrf": "…",
  "authorizations": [
    {
      "authorization_id": "43 字符随机串",
      "client_id": "DCR 注册的客户端 ID",
      "client_name": "注册时自报名称",
      "grant_type": "oauth",
      "scopes": ["lynse:read"],
      "status": "valid",
      "authorized_at": 1762680000,
      "expires_at": 1770000000
    }
  ]
}
```

- 时间均为 Unix 秒；`expires_at` 是授权的绝对截止时间。
- 按 `authorized_at` 倒序；记录与账号绑定，只能看到本人记录。
- 已吊销/已过期记录保留展示，直到用户手动删除。

## POST /platform/oauth/api/authorization-revoke

请求 `{"authorization_id": "…"}`，成功返回 `{"ok": true}`。立即终止该连接下的全部 Token（Access/Refresh 一并失效），记录转为 `revoked` 保留展示。仅 `valid` 状态可撤销；重复撤销或撤销他人记录返回 400。

## POST /platform/oauth/api/authorization-delete

请求与响应同上。物理删除一条记录，仅允许 `revoked` 或 `expired` 状态；`valid` 状态返回 400（应先撤销）。

## 状态机

```
valid ──撤销(revoke)──▶ revoked ──删除(delete)──▶ 消失
  │
  └──到达 expires_at──▶ expired ──删除(delete)──▶ 消失
```

前端按状态渲染操作：`valid` → 「撤销授权」；`revoked`/`expired` → 「删除」。

## 上线核对

- 官网页面仅通过同源代理调用以上三个端点 + 登录动作；不要在浏览器直连 MCP 服务端口。
- 客户端名称是注册时自报的，页面可展示但不要表述为「已认证平台」。
- 撤销后该连接的 MCP 调用立即失败，客户端需重新走授权流程。

## POST /platform/oauth/api/token-login

免二次登录的会话引导：调用方携带官网网页版已登录的账号 token（`{"token": "…"}`），桥接经账号后端 `customer/current` 验证后建立与短信登录同等信任级别的控制台会话，并同步刷新该账号有效授权的上游凭据。成功返回 `{"user": {"name": "…"}}`。

适用同源网页控制台（如官网 web 应用内的授权管理页）：先 `GET authorizations` 取 `csrf`，再携带 `Origin` 与 `X-MCP-CSRF` 调用。token 无效返回 400（统一中文提示，不含后端细节）；与短信登录共用限流与安全约束，token 不落明文。
