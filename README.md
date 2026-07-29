# Lynse MCP

`lynse-mcp` 是灵光记 Lynse 的独立 Model Context Protocol 服务仓库。

它把 Lynse 的会议、文件夹和待办能力暴露给 Claude、Cursor、VS Code 等 MCP 客户端使用。

## 版本

当前版本从 `0.0.1` 开始。

## 安装

需要 Python 3.11 或更高版本。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-mcp.txt
```

如果你还没有 Lynse API 客户端环境，请先在现有 Lynse 账号里完成登录配置。

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

- 所有写入和删除类工具都要求显式确认。
- 这是一个独立仓库，不再包含 `lynse-cli` 的命令入口。
