# 官网账号授权与 ECS 部署

正式入口已部署为 `https://www.lynse.cn/mcp`，连接 `api.lynse.cn` 正式账号系统；测试入口为 `https://www.lynse.ai/mcp`，连接 `gapp.lynse.cn` 测试账号系统。ChatGPT、WorkBuddy 等支持远程 MCP OAuth 的客户端共用同一环境的 MCP 地址，不需要按客户端增加 Lynse 域名。客户端通过 `/register` 登记自己的 `redirect_uri`，通过 `/authorize` 跳转官网 `/platform/oauth`；用户登录并明确允许访问后，服务返回登记的客户端回调。HTTPS、loopback 本地回调和配置允许的桌面协议均可登记，授权时严格匹配已登记地址。

官网代码在 RevelSpark 的 `src/pages/McpOAuthPage.tsx`，账号接口由 Python 服务的 `/platform/oauth/api/*` 提供，经官网 Nginx 同源代理。账号 Token 仅保存在服务端加密数据库；客户端收到独立、限权的 MCP Token。不能用本机 CLI 的共享 Key 启动多用户服务。

## 域名与回调

| 用途 | 正式环境 | 测试环境 |
| --- | --- | --- |
| 业务账号接口 | `https://api.lynse.cn` | `https://gapp.lynse.cn` |
| 现有 WorkBuddy 绑定回调 | `https://api.lynse.cn/api/auth/workbuddy/callback` | `https://gapp.lynse.cn/api/auth/workbuddy/callback` |
| MCP 入口 | `https://www.lynse.cn/mcp`（已部署） | `https://www.lynse.ai/mcp`（已部署） |
| 官网授权页面 | `https://www.lynse.cn/platform/oauth` | `https://www.lynse.ai/platform/oauth` |
| 可选微信登录回调 | `https://www.lynse.cn/platform/oauth/wechat`（未启用） | `https://www.lynse.ai/platform/oauth/wechat`（未启用） |
| MCP 客户端回调 | 客户端提供的登记地址 | 客户端提供的测试登记地址 |

WorkBuddy 的 `/api/auth/workbuddy/callback` 用于 Lynse 绑定 WorkBuddy，是已有的另一条授权流程。MCP 登录不复用这个回调。多个客户端共用登录页面、授权端点和 Token 端点，各自的回调通常属于各客户端。客户端名称是注册时的自报名称，不代表已认证的平台身份。

目前使用动态客户端注册（DCR）和 S256 PKCE。ChatGPT 支持 DCR；填写 MCP 地址并选择 OAuth 后，由客户端发起注册/授权，回调以其管理界面给出的精确地址为准。未实现 CIMD，元数据不会声明支持它。其他客户端需支持远程 Streamable HTTP MCP 和 OAuth。2026-10-10 已通过 Codex 原生客户端完成只读 OAuth、工具发现与账号/会议查询，见 [Codex 实测与调试](CODEX_TEST.md)；ChatGPT、Muse、WorkBuddy 的账号连接仍未实际验证。

参考：[OpenAI MCP 授权文档](https://developers.openai.com/plugins/build/auth)、[MCP 授权规范](https://modelcontextprotocol.io/specification/latest/basic/authorization)。

## 账号接口

手机登录复用现有 `GET /api/auth/captcha/sms` 和 `POST /api/auth/login`（`PHONE_CODE`）。登录后使用 Token 调用 `/api/business/customer/current` 获取真实账号 ID，不信任前端提交的 ID 或登录响应里的 `customerId`。

微信适配现有 `/api/auth/render` 与 `WECHAT_JUMP` 登录。当前正式接口生成 `snsapi_userinfo` 的微信内网页授权链接，回调仍是历史地址；`WECHAT_SCAN` 当前不被正式接口接受。因此默认不显示微信入口。启用前需在微信平台配置官网网页授权域名，确认后端的 appid、secret、state 校验及回调配置，设置 `LYNSE_WECHAT_CALLBACK_URL`。这不是桌面扫码登录；桌面网站扫码需要后端另行提供微信开放平台 `snsapi_login`。

## 同一台 ECS 启动

远程依赖使用 `requirements-remote.txt`。Docker 使用多阶段构建，仅依赖构建阶段包含 Git，默认 `production` 镜像不包含可选网页测试客户端。需要 `/platform/oauth/test` 的测试实例应选择 `test-ui` 构建阶段并设置 `LYNSE_MCP_TEST_UI=1`；`deploy/compose-lynse-ai-test.yml` 已选择该阶段。标准 `compose.yml` 使用生产镜像。

官网静态文件、现有后端、MCP 容器可以共用 ECS。新服务仅绑定本机 `127.0.0.1:8000`，由 Nginx 通过不同域名/路径转发。生产和测试分别运行实例、数据库和加密密钥。

```bash
cp .env.example .env
chmod 600 .env
# 在服务器执行，把输出写入 .env 的 LYNSE_MCP_ENCRYPTION_KEY。不要提交密钥。
python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
docker compose up -d --build
curl --fail http://127.0.0.1:8000/health
```

密钥生成命令需要先安装 `cryptography`，或使用已安装项目依赖的虚拟环境。保留 `.env` 的同一个密钥和 `oauth-data` 卷；更换密钥会使已有授权不可解密。运行 `docker compose down` 会保留卷；不要使用 `down -v`。备份数据时同时保管加密密钥，限制访问权限。

国内 ECS 直接复用 `www.lynse.cn` 的现有 DNS 和 HTTPS 证书，将 `deploy/nginx-lynse-cn.conf` 加入现有官网 HTTPS server；服务仅绑定 `127.0.0.1:18001`，静态授权页放在独立 release 目录。国内原配置对点路径使用 `deny all`，OAuth 元数据需使用专门的精确 location。`deploy/nginx-mcp.conf` 仅保留为另设 `mcp.lynse.cn` 子域名时的参考，需要另外配置 DNS 和证书。若官网和后端不在同一机器，官网代理到 MCP 的私网地址并限制访问来源。

国内正式环境已通过阿里云 CLI/云助手部署到 ECS `47.101.165.66`；测试环境通过腾讯云 CLI/TAT 部署到官网现有 Lighthouse 实例。两边均新增授权页与 MCP 路由，复用原有 HTTPS 证书，没有修改 DNS 或微信平台。授权页使用 RevelSpark 的独立 `vite.mcp.config.ts` 构建，不替换首页及其他官网资源。国内镜像从本地构建的官方 CLI v1.7.0 wheel 安装，依赖使用阿里云 PyPI 镜像，构建过程无需 Git/apt。操作与回退方法见 [国内部署记录](deployment-www-lynse-cn.md) 和 [测试部署记录](deployment-www-lynse-ai-test.md)。

## 本地验证

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```

本地运行时将 MCP 地址设置为 `http://127.0.0.1:8000/mcp`、官网地址设置为 Vite 的实际 loopback 地址（如 `http://localhost:5173`），数据库设置为本地可写路径，并配置加密密钥。本地官网开发服务器还需提供 `/platform/oauth` 页面，并将 `/platform/oauth/api` 代理到本机 8000；独立生产构建配置不包含本地路由代理。默认账号源仍是真实正式接口：单元测试使用模拟后端，不发送真实短信。

手动调试可以使用 `scripts/dev_oauth_client.py`，默认只监听 `http://127.0.0.1:8765`。它发起新授权，接收回调并验证授权码兑换、MCP 初始化、工具发现，最后撤销此次临时授权；不会自动读取或修改业务数据。本地配置放在 Git 忽略的 `.env.local` 中，每个 MCP/调试客户端终端均需先加载：

```bash
set -a
source .env.local
set +a
# 第一个终端
.venv/bin/python lynse_remote.py
# 第二个终端加载相同配置后
.venv/bin/python scripts/dev_oauth_client.py
```

官网使用 `npm run dev -- --host localhost --port 5173 --strictPort`。2026-10-09 的本地配置连接测试账号源 `https://gapp.lynse.cn`，需要测试环境账号。不要将 `.env.local` 中的加密密钥提交或粘贴到聊天。微信本地入口未启用。

## 授权与运行边界

- 默认 `lynse:read`；申请 `lynse:write` 才能操作文件夹、移动会议或删除待办，同时工具仍要求 `confirm=true`。
- 授权码 120 秒且单次兑换，浏览器请求绑定 Cookie 和 CSRF；授权请求 10 分钟有效。
- Access Token 最长 15 分钟，Refresh Token 每次旋转；撤销任一 Token 会撤销同一授权下的所有 Token。
- 当前未接入业务账号的长期刷新机制：授权有效期以账号登录 Token 为上限，最长 24 小时，到期需重新登录。浏览器登录态最长 1 小时。
- SDK 的 MCP 会话绑定创建它的访问 Token；访问 Token 刷新后，客户端应重新初始化 MCP 会话。
- SQLite 加密存储适用于这版单 ECS、单实例部署。横向扩容前需迁移共享数据库和限流，不能复制独立 SQLite 文件作为多个实例的状态。
- OAuth 地址含临时票据，Nginx 参考配置关闭相关访问日志；云代理/CDN 也应跳过这些路径的缓存及完整 URL 日志。不要记录短信验证码、账号 Token、MCP Token 或授权码。

上线验证应包含真实测试账号登录、客户端完整 OAuth/MCP 调用、撤销授权及生产/测试隔离。自动测试已覆盖协议与多账号隔离，但不能代替真实账号和客户端联调。

## 验证记录（2026-10-09）

26 项单元/协议测试在本地和服务器 Python 3.12 容器内通过，覆盖账号隔离、PKCE、刷新与撤销，以及可选网页测试客户端。远程账号适配器自行验证 Token 对应的账号 ID，兼容固定的 CLI v1.7.0 依赖，不依赖新版 CLI 的 owner 校验实现。本地浏览器曾以模拟账号验证登录、切换账号、允许访问、回调及授权码兑换，无真实短信或账号操作。独立授权页生产构建和修改文件的 ESLint 检查通过；全量官网 TypeScript 检查仍受现有 AboutUsPage、Header、VideoHero、MaintenancePage 错误影响。

`www.lynse.ai` 已实际构建并运行容器，Nginx 检查、健康检查和公网 OAuth/MCP 元数据验证通过。公网浏览器检查覆盖桌面及手机登录表单、客户端名称、拒绝授权回调与无横向溢出；首页文件校验值未变，原有后端服务保持运行。网页测试入口为 `https://www.lynse.ai/platform/oauth/test`，用户完成测试账号手机登录后，可验证授权码兑换、MCP 初始化与工具发现，然后自动撤销本次临时授权。真实账号短信登录及 ChatGPT/Muse/WorkBuddy 联调尚未执行；微信入口未启用。

## 国内验证记录（2026-10-10）

`www.lynse.cn/mcp` 的容器健康、Nginx 检查、OAuth 发现、未授权请求 401、动态注册、PKCE 授权跳转、浏览器 context 和拒绝回调均通过。服务器 Python 3.12 容器内 26 项协议测试通过；官网首页文件校验值未变。生产镜像未包含网页测试客户端，`/platform/oauth/test` 返回 404。新设计的授权确认页面已经部署，默认不显示微信登录。

用户确认国内连接正式账号源 `api.lynse.cn`，Codex 继续使用已验证的 `lynse_test` → `www.lynse.ai/mcp`；额外添加的 `lynse_cn` 已移除。国内正式账号完整 OAuth、会议查询与 WorkBuddy 真实安装尚未实测；已完成的 Codex 真实账号查询属于 `www.lynse.ai` 测试环境。
