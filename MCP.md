# Lynse MCP 服务说明

`lynse-mcp` 将 Lynse 的现有 API 能力暴露为标准 Model Context Protocol 工具。

## 安装

需要 Python 3.11 或更高版本。

```bash
python3 -m venv .venv

# macOS / Linux
.venv/bin/python -m pip install -r requirements-mcp.txt

# Windows PowerShell
.\.venv\Scripts\python.exe -m pip install -r requirements-mcp.txt
```

## stdio

```bash
.venv/bin/python lynse_mcp.py
```

客户端配置示例：

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

## Streamable HTTP

```bash
.venv/bin/python lynse_mcp.py --transport streamable-http --host 127.0.0.1 --port 8000
```

## 工具

- 读取：当前用户、会议列表/搜索/详情/总结/大纲/转写、文件夹、待办。
- 整理：先用 `plan_meeting_organization` 生成只读计划，再经确认调用 `apply_meeting_organization`。
- 写入：创建文件夹、移动会议。
- 删除：删除指定待办、清理已完成待办。
