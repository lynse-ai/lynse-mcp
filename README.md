# Lynse MCP

`lynse-mcp` 是灵光记 Lynse 的独立 Model Context Protocol 服务仓库。

它把 Lynse 的会议、音频文件、文件夹和待办能力暴露给 Claude、Cursor、VS Code 等 MCP 客户端使用。

## 版本

当前版本为 `0.0.4`。

## 安装

需要 Python 3.11 或更高版本。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-mcp.txt
```

首次使用需要授权时，客户端会打开本机安全页面输入 Lynse Key。Key 不会进入聊天记录或 MCP 工具参数。

## 运行

stdio 模式：

```bash
.venv/bin/python lynse_mcp.py
```

Streamable HTTP 模式：

```bash
.venv/bin/python lynse_mcp.py --transport streamable-http --host 127.0.0.1 --port 8000
```

## 连接示例

```json
{
  "mcpServers": {
    "lynse-mcp": {
      "command": "/绝对路径/.venv/bin/python",
      "args": ["/绝对路径/lynse_mcp.py"]
    }
  }
}
```

## 说明

- `get_meeting_audio` 通过 Lynse 预签名下载接口返回可访问的会议音频资源。
- `reset_lynse_key` 在用户确认后打开安全页面；新 Key 验证成功才会替换旧 Key。
- 所有写入和删除类工具都要求显式确认。
- 这是一个独立仓库，不再包含 `lynse-cli` 的命令入口。

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
