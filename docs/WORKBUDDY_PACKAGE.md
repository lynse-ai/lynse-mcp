# WorkBuddy 上传包

WorkBuddy 上传的是连接器目录包。采用远程 MCP + OAuth 时，服务端 Python 代码、依赖、虚拟环境和 Docker 配置运行在服务器，不进入上传包。保留本地 stdio 和 Lynse Desktop MCP 开关，并不要求把它们一并上传。

根据 [WorkBuddy 官方连接器文档](https://open.workbuddy.cn/docs/connector)，当前包只含三个必需文件，ZIP 根目录直接放置这些文件：

```text
connector-meta.json   # 名称、描述、中英文示例、版本和 source
mcp.json              # 唯一的远程 Streamable HTTP MCP 地址
icon.png              # 复用 Lynse Desktop 品牌图标
```

MCP 工具已经提供参数、说明和返回结构，因此省去可选 Skill 文件。包内不含账号凭据，OAuth 由 WorkBuddy 发起并通过 Lynse 服务完成；回调 `workbuddy://workbuddy/mcp/connector%3Alynse/oauth/callback` 使用元信息中的 `source=lynse`。

## 构建与上传

在仓库根目录运行（仅使用 Python 标准库）：

```bash
python3 scripts/package_workbuddy.py
```

产物为 `dist/lynse-workbuddy.zip`。上传此 ZIP，原始 `.venv`、仓库目录和 npm `.tgz` 无需上传。脚本打印压缩和解压后大小，二者均强制小于 **20,000,000 字节**；只允许三个指定文件进入包，避免缓存和配置误入。

**默认包连接测试地址 `https://www.lynse.ai/mcp`。** 国内正式入口已部署为 `https://www.lynse.cn/mcp`，国内包单独生成：

```bash
python3 scripts/package_workbuddy.py \
  --url https://www.lynse.cn/mcp \
  --output dist/lynse-workbuddy-cn.zip
```

地址覆盖只修改生成包，不改源配置。脚本拒绝明文 HTTP、URL 内凭据、查询参数和 fragment。本包不设置 `auth_mode`，按标准 OAuth 发现、动态注册与 PKCE 流程连接；中英文示例需要 WorkBuddy 4.24.0+，元信息已经声明。

ZIP 结构与大小已在本地验证，真实 WorkBuddy 安装授权与平台审核尚未完成。上传之后仍需核对实际登录、只读查询和退出登录行为。

2026-10-10 国内包为 **59,447 字节**，解压后 **62,130 字节**，包含上述三个文件；国内服务已通过公网 OAuth/PKCE/拒绝授权检查和服务器 26 项协议测试。Codex 继续使用已验证的 `www.lynse.ai`；国内正式账号完整 OAuth/查询和 WorkBuddy 真实安装尚未实测，见 [国内部署记录](deployment-www-lynse-cn.md)。

## 本地数据

上传包面向云端服务。本机会议和灵感笔记通过 Lynse Desktop 的 **设置 → AI 助手 → MCP 连接** 开关接入；直接复制 WorkBuddy 配置即可，无需把桌面程序或 Python 环境放入连接器包。
