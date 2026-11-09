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

国内正式环境已通过阿里云 CLI/云助手部署到 ECS `47.101.165.66`；测试环境通过腾讯云 CLI/TAT 部署到官网现有 Lighthouse 实例。两边均新增授权页与 MCP 路由，复用原有 HTTPS 证书，没有修改 DNS 或微信平台。授权页使用 RevelSpark 的独立 `vite.mcp.config.ts` 构建，不替换首页及其他官网资源。国内镜像于 2026-10-31 更新为固定提交构建的 CLI 1.8.5 wheel，并校验来源提交和 SHA256；依赖使用阿里云 PyPI 镜像，构建过程无需 Git/apt。操作与回退方法见 [国内最新更新](deployment-www-lynse-cn-2026-10-31.md)、[首次国内部署](deployment-www-lynse-cn.md) 和 [测试部署记录](deployment-www-lynse-ai-test.md)。

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
- 用户确认授权后，grant 拥有独立的绝对有效期（默认 90 天，`LYNSE_MCP_GRANT_DAYS`），不跟随浏览器登录会话；到期需重新授权。空闲有效期默认 30 天（`LYNSE_MCP_REFRESH_IDLE_DAYS`），Refresh Token 每次使用即重新滑动并轮换，但永远不超过绝对期；闲置 30 天后连接失效。
- Access Token 最长 15 分钟。撤销任一 Token 会立即终止该授权下的全部 Token；重放已轮换的 Refresh Token 同样吊销整条授权（RFC 9700 重放防护），伪造的随机 Token 只被拒绝、不牵连其他授权。
- 授权内保存的上游业务凭据仍只有登录会话的寿命（`expireIn` 与本地 24 小时上限取较短值；缺少时按 24 小时处理），上游没有续期接口。用户在官网重新登录时，该账号全部有效授权的上游凭据会被自动刷新，授权本体不动、无需重新确认；上游凭据过期时工具会提示回官网登录。要彻底免登录续期，需账号后端提供可刷新凭据（见下节）。
- 浏览器登录态跟随上游 `expireIn`（最长 24 小时），授权页 Cookie 与 CSRF 同步有效至 24 小时。
- 官网「我的授权」控制台可列出、撤销、删除应用授权记录，见 [应用授权记录桥接 API](authorization-records-api.md)；已吊销/已过期记录保留展示至用户删除。
- 当前 SDK 的 MCP 会话绑定客户端 ID、签发者和账号主体；同一身份刷新 Access Token 后可继续使用原会话，刷新本身不要求重新初始化。服务重启或会话失效返回 404 时，客户端仍需重新初始化；更换账号不能复用原会话。
- SQLite 加密存储适用于这版单 ECS、单实例部署，容量上限 50 万行（约数万活跃用户），单写者是吞吐上限；横向扩容前需迁移共享数据库和限流，不能复制独立 SQLite 文件作为多个实例的状态。加密密钥与数据同机是单机部署的固有取舍；迁移到后端持有授权的模型（下节）时，应同步评估 KMS 级密钥管理。
- OAuth 地址含临时票据，Nginx 参考配置关闭相关访问日志；云代理/CDN 也应跳过这些路径的缓存及完整 URL 日志。不要记录短信验证码、账号 Token、MCP Token 或授权码。

上线验证应包含真实测试账号登录、客户端完整 OAuth/MCP 调用、跨天刷新、撤销授权及生产/测试隔离。自动测试已覆盖协议、滑动轮换、重放吊销与多账号隔离，但不能代替真实账号和客户端联调。

## 长期连接与第三方客户端联调

MCP Refresh Token 用于续发最长 15 分钟的 MCP Access Token；业务账号 Token 用于调用 Lynse 业务接口。这是两层独立凭据。授权框架（grant 绝对期 + Refresh 空闲滑动轮换 + 重放吊销）已落地；上游业务凭据的寿命仍绑定登录会话，手机/微信登录适配没有可续期的上游凭据。CLI 的 `/api/auth/apikey/token` 需要 API Key，WorkBuddy 的 `/api/auth/workbuddy/token/refresh` 刷新的是 WorkBuddy 票据，均不能直接用于续期这里的手机/微信账号登录态。

目前的过渡方案：上游凭据过期时，MCP 工具会明确提示回官网重新登录；用户重新登录后，该账号全部有效授权的上游凭据自动刷新，连接恢复，授权本体与绝对期不变。要让连接彻底免登录，需账号后端提供可供此登录流程使用的可刷新凭据或等价接口，再由 MCP 接入。若后端已有该能力，只需修改 MCP 适配；否则需后端配合。单独延长 grant、MCP Refresh Token 或取消本地 24 小时上限，都不能使已过期的业务账号 Token 恢复有效。

后端评审与交付可使用 [MCP 长期账号授权实施计划](BACKEND_MCP_REFRESH_PLAN.md)；其中 MCP 侧的本地寿命模型（绝对期与空闲期分离、重放吊销、客户端记录续存）已随本版本落地，后端接口本身仍是待评审方案。

`resource` 是必填参数，授权请求、授权码兑换和刷新请求均须使用发现文档中的 MCP 资源地址。正式环境为 `https://www.lynse.cn/mcp`，测试环境为 `https://www.lynse.ai/mcp`，不能混用。缺失、空值或错误值返回 `invalid_target`，并在 `error_description` 中给出本实例要求的地址；Token 请求还会拒绝重复的 `resource`。保持资源绑定校验，第三方漏传参数应先作为客户端兼容性问题定位。

Muse 等客户端的验收应覆盖完整授权、`tools/list`、只读 `tools/call`、跨 15 分钟的 Token 刷新与会话连续调用、授权到期后的重新连接，以及撤销授权。元数据、DCR 或回调登记通过不代表完整接入成功。授权页面打不开时，先确认失败发生在哪个站点、哪个跳转环节及对应 HTTP 状态，再判断归属；不要记录包含票据、授权码或 Token 的完整 URL。

## 验证记录（2026-10-09）

26 项单元/协议测试在本地和服务器 Python 3.12 容器内通过，覆盖账号隔离、PKCE、刷新与撤销，以及可选网页测试客户端。远程账号适配器自行验证 Token 对应的账号 ID，兼容固定的 CLI v1.7.0 依赖，不依赖新版 CLI 的 owner 校验实现。本地浏览器曾以模拟账号验证登录、切换账号、允许访问、回调及授权码兑换，无真实短信或账号操作。独立授权页生产构建和修改文件的 ESLint 检查通过；全量官网 TypeScript 检查仍受现有 AboutUsPage、Header、VideoHero、MaintenancePage 错误影响。

`www.lynse.ai` 已实际构建并运行容器，Nginx 检查、健康检查和公网 OAuth/MCP 元数据验证通过。公网浏览器检查覆盖桌面及手机登录表单、客户端名称、拒绝授权回调与无横向溢出；首页文件校验值未变，原有后端服务保持运行。网页测试入口为 `https://www.lynse.ai/platform/oauth/test`，用户完成测试账号手机登录后，可验证授权码兑换、MCP 初始化与工具发现，然后自动撤销本次临时授权。真实账号短信登录及 ChatGPT/Muse/WorkBuddy 联调尚未执行；微信入口未启用。

## 国内验证记录（2026-10-10）

`www.lynse.cn/mcp` 的容器健康、Nginx 检查、OAuth 发现、未授权请求 401、动态注册、PKCE 授权跳转、浏览器 context 和拒绝回调均通过。服务器 Python 3.12 容器内 26 项协议测试通过；官网首页文件校验值未变。生产镜像未包含网页测试客户端，`/platform/oauth/test` 返回 404。新设计的授权确认页面已经部署，默认不显示微信登录。

2026-10-31 已重新添加并验证 `lynse_cn` → `www.lynse.cn/mcp`，通过国内正式账号网页 OAuth、会议正文、待办和音频读取。该连接的现有只读授权在服务升级后仍然有效。WorkBuddy 真实安装与各宿主内嵌界面仍需分别联调。`lynse_test` 及测试环境未在本次更新。
