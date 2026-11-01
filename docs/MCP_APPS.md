# MCP Apps 接入与预览

界面使用官方 `@modelcontextprotocol/ext-apps` SDK，通过 MCP Apps 标准桥接与宿主通信。Python 服务继续复用原来的账号授权、会议与待办接口。

## 工具与资源

| 展示工具 | 主要参数 | UI 资源 |
| --- | --- | --- |
| `show_meetings` | `days=7`、`keyword`、`page=1`、`page_size=20`、可选 `file_id` | `ui://lynse/meetings-v1.html` |
| `show_todos` | `status=all/open/done`、`page=1`、`page_size=20` | `ui://lynse/todos-v1.html` |
| `show_confirmation` | `question`、`options`、可选 `description` | `ui://lynse/confirmation-v1.html` |

资源 MIME 类型为 `text/html;profile=mcp-app`，展示工具通过 `_meta.ui.resourceUri` 关联资源。三个资源使用同一份自包含 HTML，按工具结果的 `view` 渲染。浏览器运行时不加载外部 JavaScript、CSS 或字体。

界面按聊天内组件设计：单列紧凑排布，去掉品牌头、宣传文案、装饰图标和空白详情占位。会议默认仅显示列表，搜索按需展开；点击一场会议后在同一块区域展示详情，并支持返回。录音控件合为一行，只有加载录音后才显示倍速和刷新。待办以状态图标、内容和来源显示，单页结果隐藏分页栏。确认框仅保留问题、可选说明、单选项和确认 / 取消操作。

筛选、翻页和详情使用仅向界面开放的 `read_meetings_view`、`read_meeting_view`、`read_todos_view` 数据工具；它们不附带新的 UI 模板，避免每次交互都打开另一张卡片。播放录音调用已有的 `get_meeting_audio`，按需获取临时链接，支持原生播放、拖动进度、倍速与刷新过期链接。

远程模式的所有数据读取复用 `AccountTools` 和当前 OAuth 身份。浏览器不接收 API Key 或上游登录令牌。会议列表不包含音频签名链接，点击播放时才获取。待办仅提供读取与状态筛选。

## 用户选择

调用示例：

```json
{
  "question": "这些会议，你想怎样整理？",
  "description": "选择方式后，继续讨论具体的整理范围。",
  "options": [
    {"id": "A", "label": "按项目归档", "description": "归入对应的项目文件夹"},
    {"id": "B", "label": "按时间整理", "description": "按周或月查看"},
    {"id": "C", "label": "先看建议", "description": "先查看建议清单"}
  ]
}
```

支持 2–12 个选项，ID 必须唯一。界面显示实际 ID，默认不选中。选中后仍需点击“确认”；成功发送后锁定按钮，防止重复提交。宿主拒绝消息时保留选择并允许重试。

`show_confirmation` 返回 `status=awaiting_user` 和独立 `requestId`，仅表示已展示。用户确认通过 `ui/message` 发送一条包含问题、说明、选项 ID 和确认编号的用户消息；取消则明确发送“请勿执行”。界面不调用写入工具，不会把某个宽泛的选项变成对其他操作的授权。需要修改数据时，继续遵守原有具体范围确认和 `lynse:write` 权限检查。

客户端不支持消息回传时，界面提示用户在聊天中回复选项编号；不支持 MCP Apps 时，工具仍返回完整的文字选项。

## 跟随主题

界面在初始化后读取宿主上下文，并订阅 `ui/notifications/host-context-changed`，切换主题时保留当前界面状态。

| 角色 | 宿主变量 |
| --- | --- |
| 强调色 | 优先 `--color-text-info`，其次 `--color-ring-primary` |
| 背景 | `--color-background-primary`、`--color-background-secondary` |
| 文字 | `--color-text-primary`、`--color-text-secondary` |
| 边框 | `--color-border-primary` |
| 字体 | `--font-sans` |

这些是 MCP Apps 的语义主题变量；宿主需要提供对应变量，才能与其颜色一致。缺省时使用本地明暗配色、青绿强调色，以及 Avenir Next / 苹方等本地字体。

## 音频域名

UI 资源的 CSP 默认允许 `https://*.lynse.cn`、`https://*.lynse.ai` 和 `https://*.aliyuncs.com` 加载媒体。实际录音若使用其他域名，在服务端环境配置追加 HTTPS 来源：

```dotenv
LYNSE_MCP_MEDIA_ORIGINS=https://media.example.com,https://storage.example.com
```

此配置由服务端管理员设置，工具参数无法修改。UI 不直接请求业务 API，`connectDomains` 为空。开发预览允许本机 HTTP 示例音频；线上录音使用 HTTPS。

## 本地构建和预览

运行服务只需现有 Python 依赖。仓库、npm 安装包和生产镜像包含已构建的 `web/dist/lynse-app.html`；使用者无需安装前端构建工具。修改界面时使用 Node.js 22+：

```bash
npm ci
npm run check:ui
npm run build:ui
.venv/bin/python scripts/preview_apps.py
```

打开 `http://127.0.0.1:8765`。预览按聊天栏宽度显示组件；展开顶部“预览设置”可切换会议、待办、选择确认，并修改明暗模式和强调色。预览仅监听本机，使用示例数据，工具调用经过真实 Python MCP 注册和序列化路径，确认消息显示在预览页面下方。预览文件不进入 npm 安装包或生产镜像。

Windows 将 Python 路径替换为 `.venv\Scripts\python.exe`。启动服务的终端若已激活虚拟环境，也可以使用 `npm run preview:ui`。

## 验证与部署边界

```bash
.venv/bin/python -m unittest discover -s tests -v
npx playwright install chromium
npm run test:ui
git diff --check
```

已有浏览器时可用 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` 指定可执行文件。浏览器测试覆盖会议搜索、分页、摘要 / 大纲 / 转写、实际示例音频播放与倍速、链接刷新、待办筛选与来源跳转、确认 / 取消 / 重试、动态主题、窄屏与键盘操作，以及文本安全显示。

Python 测试覆盖 UI 资源与工具元信息、真实协议调用、参数校验、列表格式转换、文本回退、账号隔离与最小安装包资源读取。npm 和两种生产 Dockerfile 已加入资源文件；生产运行时不包含本地预览服务器。

2026-10-31 已更新国内正式入口 `www.lynse.cn/mcp`，提供 23 个工具和 3 个 UI 资源。原生 MCP 客户端已验证资源读取、会议/待办视图工具，以及三场真实会议的摘要、大纲、转写正文和音频资源；正文适配支持正式 API 的 `conclusionText`、`outlineText` 字段。容器内 64 项测试通过。详见 [正式环境更新记录](deployment-www-lynse-cn-2026-10-31.md)。

真实宿主中的界面承载、音频播放及 CSP 仍需在支持 MCP Apps 的目标客户端联调；重新连接可刷新工具列表。`www.lynse.ai` 测试入口未在本次更新。

官方参考：[MCP Apps](https://modelcontextprotocol.io/extensions/apps/overview)、[为 MCP 服务添加界面](https://developers.openai.com/plugins/build/chatgpt-ui)。
