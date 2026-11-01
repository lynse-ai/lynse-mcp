# www.lynse.cn MCP 正式更新（2026-10-31）

仅操作用户指定实例 `i-uf6j5zu4kakq7pwsnxpt`（`lynse-web`，`cn-shanghai`，`47.101.165.66`）。所有 ECS 查询、云助手命令与文件上传均显式指定该实例；其他服务器未执行变更。

## 已生效

- 当前 release：`/opt/lynse-mcp-cn/releases/20261031-122820`。
- 当前镜像：`lynse-mcp-cn:20261031-122820`；容器 `lynse-mcp-cn-mcp-1` 已为 `healthy`。
- MCP SDK 保持 2.0.0，CLI 从 1.7.0 更新到 1.8.5，来源固定提交 `52c604f1343c196496cce91629a872e947c08256`。
- 账号工具仅返回允许的资料字段，线上已确认不存在 password、API Key、Token 等敏感字段。
- 移动工具允许空目标，表示返回未分类；线上只读连接已确认该参数通过校验，随后按预期被写权限检查拒绝。
- 提供 23 个工具与 3 个 UI 资源；摘要、大纲适配正式字段 `conclusionText`、`outlineText`。
- OSS 对象缺失时返回明确错误，签名资源不会被当成可用录音返回；可用录音正常返回资源链接。

## 构建、备份与验证

- 白名单包 302023 字节，SHA256：`460e354352410affc28fda23f5e337713abe694f0d86e111c3314f5258c2c4c4`；37 个源文件/测试输入/wheel 哈希校验通过。
- ECS 上实际完成生产 Docker 镜像构建；容器内 64 项测试通过（无 Node 的生产镜像中，3 项可选 Node 启动器测试跳过）。本地现有环境与全新 Python 3.12 环境的 64 项测试全部通过，10 项本地界面测试通过。
- 保留数据卷 `lynse-mcp-cn_oauth-data` 与原加密配置；更新前生成加密授权库备份 `/data/backup-20261031-122820.sqlite3`。
- 官网首页及授权页 SHA256 与发布前一致；授权页及其原有资源已从旧 release 保留。
- Nginx 配置检查通过；内部健康检查通过；公网无令牌 MCP 返回预期 401，授权页返回 200。
- 原有 `lynse_cn` 正式账号只读 OAuth 授权可继续使用，无需重新登录。
- 原生 MCP 客户端确认 23 个工具、3 个 HTML 资源可读取；`show_meetings`、`show_todos` 和 `read_meeting_view` 返回真实数据。
- 抽检三场会议，普通工具与界面数据工具均能读取非空摘要、大纲、转写；三个音频工具调用均通过云端可用性检查并返回资源链接。
- 新增 `get_transcription_status` 在真实账号返回 `code=200`。
- 移动、提交转写和自动转写的只读权限拦截均符合预期；没有移动会议、执行归档、删除待办或提交计费转写任务。

## 验证边界

真实录音在前序复测中已通过 HTTP 206、格式识别和 1 秒实际解码；旧账号单个缺失对象不代表音频服务整体故障。本次没有恢复或重传那个缺失对象。

MCP Apps 资源、工具和真实正文已在协议层验证。本地浏览器交互测试通过；各真实宿主内嵌界面的视觉承载、音频播放和 CSP 仍需分别验证。已有客户端重新连接 `lynse_cn` 可刷新工具清单。

## 回退信息

旧 release 和旧镜像均保留：`/opt/lynse-mcp-cn/releases/20261010-072029`、`lynse-mcp-cn:20261010-072029`。

需要回退时，仅在该实例执行：

```sh
export LYNSE_MCP_IMAGE=lynse-mcp-cn:20261010-072029
docker compose --project-name lynse-mcp-cn \
  -f /opt/lynse-mcp-cn/releases/20261010-072029/deploy/compose-lynse-cn.yml up -d --no-build
python3 /opt/lynse-mcp-cn/releases/20261010-072029/deploy/activate-lynse-cn.py
```

正常回退保留当前授权数据卷。未创建 Git commit、未推送仓库或发布版本；本次部署由已记录哈希的源码快照构建。
