# Lynse Desktop MCP

桌面端代码已加入 MCP 开关，重新构建桌面应用后可用。它直接使用现有 Rust 和桌面数据接口，不打包 Python、Node 或独立 MCP 进程；原来的独立 stdio 服务和远程 OAuth 服务继续保留。

## 使用

1. 打开 **设置 → AI 助手 → MCP 连接**。
2. 开启 **允许本机 AI 访问 Lynse**。默认关闭，默认地址 `http://127.0.0.1:27125/mcp`；可在关闭时修改端口。
3. 复制 Codex 或 WorkBuddy 配置并合并到对应客户端。配置带本机专用 Bearer 凭据，不含 Lynse 账号登录 token。
4. 保持 Lynse Desktop 运行。本地数据离线可读；云端工具需要桌面端已登录。

更换访问凭据后，重新复制客户端配置。关闭开关会停止监听并取消尚未完成的请求。开启状态和本机凭据保存在应用数据目录的 `desktop-mcp.json`，Unix 文件权限为 `0600`；启动桌面应用时恢复已开启的服务。端口冲突会启动失败，设置页可修改端口后重试。

连接使用 Streamable HTTP，支持 JSON 响应；本地服务不提供 OAuth、浏览器跨域访问或公网监听。若客户端把 loopback 请求送进系统代理，应将 `127.0.0.1` 和 `localhost` 加入客户端的代理绕过名单。

## 数据范围

首版提供 10 个只读工具：

| 工具 | 内容 |
| --- | --- |
| `list_local_meetings` | 本地录音会议列表，可按关键词查询 |
| `get_local_meeting` | 本地会议详情、转写或已导出的 Markdown 笔记 |
| `list_local_notes` / `get_local_note` | 灵感笔记及语音转写；遵守 AI 上下文开关，排除受限笔记 |
| `list_local_todos` | 本地待办，可按完成状态筛选 |
| `list_cloud_meetings` / `get_cloud_meeting` | 当前账号的云端会议列表、详情、总结、大纲或转写 |
| `list_cloud_todos` | 当前账号的云端待办 |
| `list_cloud_folders` | 当前账号的云端文件夹 |
| `get_cloud_user` | 当前账号的基础资料，不返回凭据或联系方式 |

本地转写服务配置和音频文件路径不返回给 MCP。云端调用固定业务接口，只使用当前登录会话；账号退出、切换或服务地址变化时丢弃旧响应。桌面端接口不接受外部指定用户身份，也不暴露任意 HTTP 请求或文件读取工具。文件夹整理等写入操作继续使用独立 MCP 的确认流程。

## Codex / WorkBuddy / Muse

- Codex 和 WorkBuddy 的本机客户端使用设置页复制的 HTTP 配置，也可继续使用独立 Python stdio 服务。
- muse.ai 网页端连接器使用远程 HTTPS 服务，不能直接读取用户电脑的 loopback 地址。本地内容若需要在网页端使用，应先通过产品支持的同步方式进入云端。
- [Muse Connector Platform](https://muse.ai/platform) 公开说明提交与审核流程；本项目尚未完成 Muse 账号联调和连接器审核。

## Obsidian 的参考方式

[Obsidian Local REST API 社区插件](https://github.com/coddingtonbear/obsidian-local-rest-api) 在桌面应用内部启动服务，在插件设置中开启 MCP，并通过 API Key 鉴权；其 MCP 地址默认使用 `https://127.0.0.1:27124/mcp/`，也支持可选 HTTP。这是社区插件能力。

Lynse 采用相同的应用内服务开关方式，使用本机 HTTP 与独立访问凭据，省去本地证书配置；云端数据复用桌面端登录状态。

## 实现与验证

桌面仓库：`lynse-app/lynse-desktop`。

- 原生监听与鉴权：`apps/tauri/src-tauri/src/desktop_mcp.rs`。
- 工具定义：`apps/tauri/src-tauri/src/mcp-tools.json`，编译时嵌入应用。
- 数据与事件桥接：`apps/tauri/src/mcp-data.ts`、`mcp-bridge.ts`。
- 设置入口：`packages/views/settings/mcp-config-card.tsx`，含五种语言文案。

已通过原生编译、HTTP 协议与鉴权测试、数据隔离测试及设置组件测试。完整桌面 TypeScript 检查仍有修改前已存在的 `event-guard.ts` 类型错误和 `permission-preview.tsx` 导出路径错误；本次新增代码没有类型错误。真实 Codex、WorkBuddy 和 Muse 账号连接尚未完成联调。
