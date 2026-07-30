# Lynse MCP

`lynse-mcp` 是灵光记 Lynse 的独立 Model Context Protocol 服务仓库。

它把 Lynse 的会议、音频文件、文件夹和待办能力暴露给 Claude、Cursor、VS Code 等 MCP 客户端使用。

## 版本

当前版本为 `0.0.3`。

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
