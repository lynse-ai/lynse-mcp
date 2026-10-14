# 上传 WorkBuddy 前的 Codex 测试

Codex 和 WorkBuddy 使用同一环境的远程入口：`https://www.lynse.ai/mcp` 连接 `gapp.lynse.cn` 测试账号系统，`https://www.lynse.cn/mcp` 连接 `api.lynse.cn` 正式账号系统。两个环境独立授权。远程 OAuth 不依赖本机 Lynse Key，也不会复用本机账号身份。

## 本次实测

2026-10-10 已通过 Codex 原生 MCP 客户端完成：

| 检查 | 结果 |
| --- | --- |
| OAuth 服务发现、动态注册与 PKCE 回调 | 通过 |
| 授权范围 | 仅 `lynse:read` |
| MCP 初始化、工具发现 | 通过 |
| `get_auth_status` | `account_oauth`，授权有效 |
| `get_current_user` | 账号接口返回 `code=200` |
| `list_meetings(days=7)` | 返回 `code=200`，2 条会议 |

测试使用 Codex App Server 的工具调用接口，不启动额外模型，不保存测试聊天，不执行 Lynse 写操作；测试记录不包含账号信息、会议标题或 Token。此结果验证 Codex 接入，WorkBuddy 自身的 OAuth 回调和 Muse 接入仍需各自实测。

## 在 Codex 中继续调试

按用户确认，本机 Codex 继续使用已授权的 `lynse_test` → `https://www.lynse.ai/mcp`。国内 `https://www.lynse.cn/mcp` 连接正式账号源，供 WorkBuddy 接入；额外添加的 `lynse_cn` 已移除。国内服务的公网协议检查通过，正式账号完整 OAuth 和查询尚未实测；上表是已经完成的测试环境结果。国内部署详情见 [部署记录](deployment-www-lynse-cn.md)。

本机已添加 `lynse_test` 并完成登录。启动新的 Codex 会话后，可发送：

> 使用 lynse_test 检查授权状态，然后列出最近 7 天的会议。只读查询。

在其他机器首次配置时，将以下配置合并到 `~/.codex/config.toml`，然后登录：

```toml
[mcp_servers.lynse_test]
url = "https://www.lynse.ai/mcp"
```

```sh
codex mcp login lynse_test --scopes lynse:read --oauth-client-registration dcr
codex mcp get lynse_test --json
```

按浏览器提示完成测试账号登录。授权码、验证码和 Token 无需粘贴到聊天或写入 WorkBuddy 包。

如果同时安装旧版本地 Lynse 插件，请在调试指令中明确指定 `lynse_test`。本次旧插件 `0.0.3` 的生产 API Key 换 Token 接口返回 HTTP 500；这与通过测试的远程 OAuth 连接是不同路径。

## 授权完成页面

Codex 显示的 `127.0.0.1:<port>/callback` 完成页由 Codex 客户端生成。Lynse 自己的授权页源码位于 `/Users/lynse/Documents/lynse/RevelSpark/src/pages/McpOAuthPage.tsx`。

Lynse 页面现已增加授权确认状态，成功提交授权后展示品牌页面，1.5 秒后继续原有客户端回调，也可以点击按钮立即返回。确认状态仅在服务端接受授权并返回回调地址后出现；完整成功页的设计预览仅在 Vite 开发模式提供，不会在生产绕过登录。

本地启动官网开发服务后，打开 `/mcp-auth.html?preview=success&lang=zh` 查看成功页设计。授权确认状态已随国内 MCP 部署到 `www.lynse.cn`，仅在真实授权被服务端接受后显示。开发预览参数在生产构建无效；此页面不能替换 Codex 原生的本机回调页。

参考：[Codex MCP 配置](https://developers.openai.com/codex/mcp)、[Codex App Server](https://developers.openai.com/codex/app-server)。
