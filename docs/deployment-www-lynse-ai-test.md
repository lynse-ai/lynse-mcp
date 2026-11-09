# www.lynse.ai MCP 测试部署

部署日期：2026-10-09。通过腾讯云官方 CLI 的 TAT `RunCommand` / `DescribeInvocationTasks` 完成上传、构建、测试和接入。

## 使用

- 网页调试入口：<https://www.lynse.ai/platform/oauth/test>。
- MCP 客户端填写：<https://www.lynse.ai/mcp>，认证选择 OAuth。
- 账号接口：`https://gapp.lynse.cn`，使用测试环境账号手机验证码登录。
- `/platform/oauth` 需从测试入口或 MCP 客户端跳转，直接访问没有有效授权请求。
- 网页测试仅申请 `lynse:read`；授权后检查授权码兑换、MCP 初始化、工具发现，并关闭会话、撤销此次授权，不自动读取会议。
- 微信登录未启用；未完成真实账号短信登录或 ChatGPT/Muse/WorkBuddy 联调。当前授权为 90 天绝对期 + 30 天空闲滑动轮换（`LYNSE_MCP_GRANT_DAYS` / `LYNSE_MCP_REFRESH_IDLE_DAYS` 可调）；闲置 30 天或到期后需重新授权，上游业务凭据过期时回官网重新登录即可刷新。

## 服务器和文件

| 项目 | 值 |
| --- | --- |
| 腾讯云产品 / 地域 | Lighthouse / `na-siliconvalley` |
| 实例 | `lhins-m5rp3ob7`，`hermes` |
| 官网公网 IP | `43.130.12.243` |
| 发布目录 | `/opt/lynse-mcp-ai-test/releases/ai-test-20261009-103918` |
| 当前发布 | `/opt/lynse-mcp-ai-test/current` |
| Compose 项目 | `lynse-mcp-ai-test` |
| 容器 | `lynse-mcp-ai-test-mcp-1` |
| 本机端口 | `127.0.0.1:18000` → 容器 `8000` |
| 私有环境配置 | `/etc/lynse-mcp-ai-test/environment`，权限 `0600` |
| 加密 SQLite 卷 | `lynse-mcp-ai-test_oauth-data`，容器 `/data/oauth.sqlite3` |
| 独立授权页资源 | `/var/www/revelspark/platform/oauth-static` |
| Nginx 站点 | `/etc/nginx/sites-enabled/revelspark` |
| 新增 include | `/etc/nginx/snippets/lynse-mcp-ai-test.conf` |
| 代理配置 | `/etc/nginx/snippets/lynse-mcp-ai-test-proxy.conf` |
| 接入前站点备份 | `/opt/lynse-mcp-ai-test/backups/20261009-105451-revelspark.conf` |
| 最近部署记录 | `/opt/lynse-mcp-ai-test/last-deployment.json` |

加密密钥在服务器生成并保留，没有上传本地 `.env.local`。更新时保留环境文件和数据卷；不能删除卷或重新生成密钥。数据备份需同时保管该密钥，限制访问权限。

官网首页、营销资源、newsletter 接口以及 3100/3101 的现有服务均保留。复用现有域名和 HTTPS 证书，没有修改 DNS。Nginx 只对 MCP/OAuth 路径代理到新容器；授权页独立发布，并设置 HTML 类型、禁止缓存及 referrer、限制脚本来源，相关路径关闭访问日志。

## 维护

服务器上进入 `/opt/lynse-mcp-ai-test/current` 后使用：

```sh
docker compose -p lynse-mcp-ai-test -f deploy/compose-lynse-ai-test.yml ps
docker compose -p lynse-mcp-ai-test -f deploy/compose-lynse-ai-test.yml logs --tail 80
curl --fail http://127.0.0.1:18000/health
nginx -t
```

CLI 提交脚本工具为本仓库 `scripts/tat_cli.py`，本次 CLI 安装在本机临时目录 `/tmp/lynse-mcp-tccli/bin/tccli`，凭据使用既有默认配置；临时目录清理后需重新安装 CLI。

```sh
python3 scripts/tat_cli.py --cli /tmp/lynse-mcp-tccli/bin/tccli run /tmp/server-check.sh --timeout 120
python3 scripts/tat_cli.py --cli /tmp/lynse-mcp-tccli/bin/tccli poll inv-实际返回的ID
```

授权页源码位于 `/Users/lynse/Documents/lynse/RevelSpark`，通过独立入口构建：

```sh
node node_modules/vite/bin/vite.js build --config vite.mcp.config.ts --outDir /tmp/lynse-ai-mcp-oauth-build
```

下一次发布应使用新的 release 目录，打包显式选择的 Python 源码、依赖、Dockerfile、deploy、tests，并将该构建结果放入发布包 `web/`。不要打包本地环境文件、OAuth 数据库或整个官网目录。`deploy/install-lynse-ai-test.sh` 会构建镜像、运行容器内测试，通过后启动服务、发布授权页，并校验 Nginx 后 reload。

停止测试时，检查当前站点与备份的差异，移除本次新增的 `include /etc/nginx/snippets/lynse-mcp-ai-test.conf;`；`nginx -t` 通过后 reload，再使用上述 Compose 配置停止容器。保留配置、密钥及数据卷。不要直接覆盖整个站点配置，以免覆盖后续其他部署。

## 已验证

- 本地及服务器 Python 3.12 镜像内 26 项单元/协议测试通过；镜像使用 MCP SDK 2.0.0 和固定 CLI v1.7.0，账号身份校验由远程适配器提供。
- 容器 Docker health 为 `healthy`，Nginx 和原有 `revelspark-api` 为 active，既有两个容器保持运行。
- 公网 `/mcp` 无 Token 返回 401，挑战指向正确资源元数据；issuer 为 `https://www.lynse.ai/`，资源为 `https://www.lynse.ai/mcp`，发现端点支持 CORS。
- 公网桌面 1280px 与手机 390px 浏览器检查通过：授权跳转、客户端名称、手机号/验证码表单、拒绝回调，无页面异常或横向溢出。
- 首页文件 SHA256 部署前后均为 `4fd40d0f808c18aba8f50d4a64ed160a40d14cc961c22b98c00edf55990a2371`；首页、logo、字体、中文用户协议及隐私页面可访问。

## 2026-11-09 发布（v0.2.0：长期授权与授权记录）

发布目录 `/opt/lynse-mcp-ai-test/releases/ai-test-20261109-163316`，内容：应用授权记录控制台、grant 与浏览器登录会话解耦（90 天绝对期）、Refresh Token 30 天滑动轮换 + 重放吊销整族、官网重登录自动刷新 grant 上游凭据、浏览器会话放长至 24 小时、OAuth 存储索引化与容量提升。未改动授权页前端（RevelSpark），沿用已发布页面。

打包为源码、依赖、Dockerfile、deploy、tests、web。本次未打包 `scripts/`，镜像内测试剔除 `test_aliyun_cli` / `test_vendor_cli` / `test_workbuddy_package`（依赖 `scripts/` 的本地打包工具测试）；后续发布应连同 `scripts/` 打包或维持显式排除。

流程复述：TAT 分块上传（33,000 字节对齐分块，避免每块 base64 填充截断 GNU `base64 -d`）→ 服务器解码并 sha256 校验 → 安装脚本构建镜像、容器内跑测试（67 项 OK）、滚动重建容器、健康检查、激活 Nginx。安装前备份与首页校验值由 `activate-lynse-ai-test.py` 记录（首页 `8a95b3b7…`）。

公网验证通过：`/mcp` 未认证 401 且 `WWW-Authenticate` 指向资源元数据；发现文档 issuer `https://www.lynse.ai/`、resource `https://www.lynse.ai/mcp`；DCR 动态注册可用；`/authorize` 返回 302 票据跳转；`/platform/oauth/test` 与控制台页未登录渲染正常（下发 CSRF 与登录表单）。

待真实账号验证：测试账号短信登录 → 授权一个客户端 → 控制台列表/撤销/删除 → 授权后跨 15 分钟观察 refresh 轮换与会话连续 → 官网重新登录后工具调用恢复（上游凭据刷新路径）。跨 24 小时真实连接与闲置 30 天失效依赖后续观察，自动测试已用虚拟时钟覆盖对应边界。

2026-11-09 起，官网 web 应用（lynse-desktop 仓库 `apps/web`）部署在同一域名 `https://www.lynse.ai/app/`（同源子路径，Node 容器 `127.0.0.1:3102`），其应用授权管理页同源调用本文的 `/platform/oauth/api/*` 桥接，并通过 `token-login` 动作用网页版账号 token 静默建立控制台会话。`/app` 相关 nginx 片段由 lynse-desktop 仓库 `deploy/www-lynse-ai/` 管理，注入同一个 `revelspark` 站点，与本服务的 snippet 互不覆盖。
