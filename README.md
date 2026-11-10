# Lynse MCP

灵光记的会议、音频、文件夹和待办工具，当前版本 `0.2.0`。保留远程账号授权与本地 stdio 两种方式；Lynse API 客户端复用 `lynse-cli`，不在此仓库复制。

## 远程接入

国内正式入口已部署为 `https://www.lynse.cn/mcp`，连接 `api.lynse.cn` 正式账号系统；`https://www.lynse.ai/mcp` 连接 `gapp.lynse.cn` 测试账号系统。远程模式使用 Streamable HTTP + OAuth，用户在官网登录并授权，客户端无需安装 Python 或 Node。

用户在官网登录后可在「我的授权」控制台查看和管理已授权的 MCP 应用：有效授权可随时撤销（立即终止该连接的全部 Token），已吊销/已过期记录保留展示直至手动删除。浏览器走 `/platform/oauth/api/*` 桥接接口，契约见 [应用授权记录桥接 API](docs/authorization-records-api.md)；`LYNSE_MCP_TEST_UI=1` 时服务在 `/platform/oauth/test/authorizations` 提供等价的自测管理页。

### Codex

Codex 继续使用已经验证通过的 `www.lynse.ai` 入口；国内 `www.lynse.cn` 入口供 WorkBuddy 正式账号接入。

合并到 `~/.codex/config.toml`：

```toml
[mcp_servers.lynse_test]
url = "https://www.lynse.ai/mcp"
```

也可通过 CLI 配置并登录：

```bash
codex mcp add lynse_test --url https://www.lynse.ai/mcp
codex mcp login lynse_test --scopes lynse:read --oauth-client-registration dcr
```

参考 [Codex MCP 文档](https://developers.openai.com/codex/mcp)。2026-10-10 已完成 `www.lynse.ai` 的 Codex 原生客户端只读接入实测；2026-10-31 已完成国内正式入口的真实账号 OAuth、会议正文、待办及音频查询，并部署 23 个工具和 3 个 UI 资源，见 [正式环境更新记录](docs/deployment-www-lynse-cn-2026-10-31.md)。调试步骤见 [Codex 实测记录](https://github.com/lynse-ai/lynse-mcp/blob/main/docs/CODEX_TEST.md)。

### WorkBuddy

在自定义 MCP 连接配置中使用：

```json
{
  "mcpServers": {
    "lynse": {
      "type": "streamableHttp",
      "url": "https://www.lynse.cn/mcp"
    }
  }
}
```

由客户端完成 OAuth 登录。国内上传包使用 `python3 scripts/package_workbuddy.py --url https://www.lynse.cn/mcp --output dist/lynse-workbuddy-cn.zip` 生成，只包含元信息、远程 MCP 配置和图标，强制小于 20 MB。未指定 `--url` 的默认包仍接测试地址。见 [上传包说明](https://github.com/lynse-ai/lynse-mcp/blob/main/docs/WORKBUDDY_PACKAGE.md) 和 [WorkBuddy 官方文档](https://open.workbuddy.cn/docs/connector)。

### Muse（muse.ai 网页应用）

使用远程 HTTPS 服务。[Muse Connector Platform](https://muse.ai/platform) 公开提供连接器提交、审核和目录发布流程；公开页面未说明任意自定义 MCP URL 的具体配置格式。本仓库未完成 Muse 真实账号联调，不能将 Muse Code 的 `settings.json` 示例用于 muse.ai。提交与连接时需核对平台实际提供的 OAuth 回调和认证要求。

## 本地接入

需要 Python 3.11+。从仓库安装本地运行依赖：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-mcp.txt
```

Windows 使用 `.venv\Scripts\python.exe`。依赖固定到下方转写接口说明中的 CLI 提交和 MCP SDK 2.0.0；本地源码联调可用：

```bash
.venv/bin/python -m pip install -e ../lynse-cli "mcp==2.0.0"
```

Codex 本地配置（替换绝对路径）：

```toml
[mcp_servers.lynse]
command = "/绝对路径/lynse-mcp/.venv/bin/python"
args = ["/绝对路径/lynse-mcp/lynse_mcp.py"]
tool_timeout_sec = 360
```

WorkBuddy 本地配置：

```json
{
  "mcpServers": {
    "lynse": {
      "type": "stdio",
      "command": "/绝对路径/lynse-mcp/.venv/bin/python",
      "args": ["/绝对路径/lynse-mcp/lynse_mcp.py"]
    }
  }
}
```

stdio 由客户端启动，工作目录可以任意。首次调用通过安全本机页面输入 Lynse Key，客户端需支持 MCP URL elicitation；Key 不进入聊天或工具参数。已有 `~/.lynse/config.json` 授权也可复用。远程网页登录可避免客户端缺少 URL elicitation 的限制。

npm 包只含本地运行文件。Node 启动器优先使用 `LYNSE_MCP_PYTHON` 指定的 Python，其次使用包内 `.venv`，再检查系统 Python；npm/npx 不会自动安装 Python 依赖。本地源码可运行 `node bin/lynse-mcp.js`（需 Node 18+）。

Lynse Desktop 已加入 **设置 → AI 助手 → MCP 连接** 开关，重新构建桌面应用后可直接读取本地和已登录账号的云端数据，无需安装 Python 或 Node。开启后复制 Codex / WorkBuddy 配置即可；详见 [Lynse Desktop 本地 MCP](https://github.com/lynse-ai/lynse-mcp/blob/main/docs/DESKTOP_MCP.md)。本地地址仅供同机客户端使用；muse.ai 云端连接器使用远程地址。

## 工具与授权

- 授权：`get_auth_status`；本地另有 `authorize_lynse`、`reset_lynse_key`。
- 读取：用户、会议列表/搜索/详情/总结/大纲/转写、文件夹和待办。
- 音频：`get_meeting_audio` 返回预签名资源链接，过期后重新调用，不嵌入长音频。
- 整理：先调用只读的 `plan_meeting_organization`，确认后再执行 `apply_meeting_organization`。
- 写入：创建文件夹、移动会议、删除待办均需 `confirm=true`。

远程默认 `lynse:read`，写入还需 `lynse:write`。本地替换 Key 仅在新 Key 验证成功后生效；环境凭据需在环境中更新后重启。

## MCP Apps 界面

支持 MCP Apps 的客户端可以调用三个展示工具：

| 工具 | 界面 |
| --- | --- |
| `show_meetings` | 会议列表、标题搜索、分页、摘要 / 大纲 / 转写详情，以及录音播放、倍速和链接刷新；传入 `file_id` 可直接打开一场会议 |
| `show_todos` | 待办列表、全部 / 待完成 / 已完成筛选、截止日期、分页与来源会议 |
| `show_confirmation` | A/B/C 等单选选项；先选择，再确认，支持取消并回传到对话 |

界面跟随宿主传入的明暗主题与颜色变量，缺少强调色时使用青绿色。普通 MCP 客户端继续收到文本和结构化结果。待办界面只读；展示选择框只返回 `awaiting_user`，用户点击确认后才通过 `ui/message` 发送选择，不会直接执行写入。

国内正式入口 `www.lynse.cn/mcp` 已于 2026-10-31 部署这些工具；原生 MCP 协议已验证 UI 资源和真实会议正文读取。客户端重新连接后可刷新工具清单；实际界面渲染取决于客户端的 MCP Apps 支持，宿主内播放仍需在对应客户端联调。本次未更新 `www.lynse.ai` 测试服务。

本地构建与交互预览见 [MCP Apps 接入说明](docs/MCP_APPS.md)。

## 开发与部署

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```

依赖按用途分开：`requirements-mcp.txt` 用于本地，`requirements-remote.txt` 用于远程，`requirements-dev.txt` 用于测试。生产 Docker 镜像不带 Git 或网页测试客户端，测试部署显式选择 `test-ui` 构建阶段；测试和部署资料留在仓库，不进入 npm 包。

多用户服务运行 `lynse_remote.py`，部署说明见 [官网账号授权与 ECS 部署](https://github.com/lynse-ai/lynse-mcp/blob/main/docs/ACCOUNT_OAUTH.md)。`lynse_mcp.py --transport streamable-http` 仅用于个人 loopback 调试，不作为公网多用户服务。

`www.lynse.cn` 已通过阿里云 CLI/云助手部署到上海 ECS `47.101.165.66`，使用独立容器、账号授权数据库和静态页面目录，复用现有官网 HTTPS。部署与回退记录见 [国内部署记录](docs/deployment-www-lynse-cn.md)。

## 云端录音转写

CLI 和 MCP 使用 `lynse-cli` 中同一份 API 客户端与 `TRANSCRIPTION_OPERATIONS` 接口定义。
本次依赖固定到 CLI 提交 `52c604f1343c196496cce91629a872e947c08256`，避免安装后加载旧客户端副本；没有发布新的 npm 版本。

| MCP 工具 | CLI 命令 | 行为 |
| --- | --- | --- |
| `transcribe_meeting` | `meetings transcribe` | 提交转写和总结，返回任务 ID，可能消耗账户额度 |
| `get_transcription_status` | `meetings transcribe-status` | 只读查询转写进度，每次最多 100 个文件 ID |
| `auto_transcribe_new_recordings` | `meetings auto-transcribe --once` | 单次检测云端新增录音并提交，首次仅建立基线 |

提交和自动检测需 `confirm=true`，表示用户已经授权本次操作或相同范围的持续处理。
远程连接还需 `lynse:write`；状态查询只需读取权限。
自动检测工具只执行一轮，持续处理由已授权的客户端/定时任务重复调用，不在 MCP 调用中启动无限循环。
上传未完成的录音会留待后续扫描；已有任务、历史录音和非音频文件会跳过。
结果中的 `needsReview` 表示提交结果不明确，应检查服务端状态，不自动重复提交。
返回任务 ID 代表已提交，不能据此声称转写已完成。

CLI 与 MCP 默认共用 `~/.lynse/auto-transcribe/` 下按账户及 API 地址隔离的状态。
服务端管理员可设置 `LYNSE_AUTO_TRANSCRIBE_STATE_DIR=/data/auto-transcribe`，并持久化该目录。
MCP 客户端不能指定服务器状态文件路径；工具结果隐藏服务器路径和上游错误诊断正文。

协议测试检查 MCP 注册名称、读写声明、参数签名和批量上限与 CLI 的共享定义一致。
更新接口时先修改 CLI 并提交，然后更新 `requirements.txt` 的 CLI 提交固定值，运行双方测试。
本地开发可使用 `python3 -m pip install -e ../lynse-cli`，不复制 `lynse.py` 到 MCP 仓库。
