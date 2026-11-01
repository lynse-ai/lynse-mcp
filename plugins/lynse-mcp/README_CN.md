# Lynse MCP

把 ZCode 接入[灵光记 Lynse](https://www.lynse.ai)，让 AI Agent 直接使用你的会议、
总结、大纲、转写、音频、文件夹和待办。

## Agent 获得的能力

本插件注册一个远程 MCP 服务（`lynse`），提供 16 个工具：

**读取**（默认 `lynse:read` 授权即含）：

- `get_current_user` — 当前连接的账号信息
- `list_meetings` / `search_meetings` — 最近会议列表、按标题搜索
- `get_meeting` — 会议详情、总结、大纲或转写全文
- `get_meeting_audio` — 预签名音频下载链接（资源链接）
- `get_transcription_status` — 会议转写进度
- `list_folders` / `list_todos` / `plan_meeting_organization`

**写入**（仅在授权页授予 `lynse:write` 后可用）：

- `transcribe_meeting` / `auto_transcribe_new_recordings` — 发起转写
- `apply_meeting_organization` — 执行会议整理计划
- `create_folder` / `move_meetings`
- `delete_todos` / `clear_completed_todos`（破坏性操作，需确认）

除 scope 控制外，每个写工具都必须显式传 `confirm=true`——Agent 必须先说明
改动内容并获得用户同意才能执行。

插件内置 `lynse` 技能，教 Agent 完成会议回顾、待办盘点等工作流。

## 授权方式

服务端受 OAuth 保护。添加服务时，ZCode 会打开灵光记授权页
（`www.lynse.cn/mcp/authorize`），用微信或手机号登录并确认 `meetings:read`
授权即可。Lynse 凭据不会出现在聊天、工具参数或插件配置中，Agent 只持有
可随时撤销的 OAuth 令牌。

## 副作用与依赖

- **网络访问**：仅访问 `https://www.lynse.cn`（MCP 端点与 OAuth 授权服务）；
  音频链接指向灵光记对象存储。
- **MCP 服务**：提供由 lynse.ai 运营的远程 `lynse` MCP 服务
  （部署说明见 [lynse-mcp 仓库](https://github.com/lynse-ai/lynse-mcp)）。
- **无本地文件写入、无命令执行、无 hooks**；插件不打包任何二进制或第三方代码，
  对灵光记数据的修改全部经 MCP 工具并逐次确认。
- **scope 分级**：连接默认申请 `lynse:read`；写工具仅在用户于授权页授予
  `lynse:write` 后可用。带本地凭据的本地（stdio）部署见
  [lynse-mcp 仓库](https://github.com/lynse-ai/lynse-mcp)。

## 使用条件

- 拥有灵光记账号且至少有一场已录制的会议。
- 可访问 `www.lynse.cn` 的出站 HTTPS 网络。

## 许可证

MIT。本插件在 [lynse-mcp 仓库](https://github.com/lynse-ai/lynse-mcp)维护。
