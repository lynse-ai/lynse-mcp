# lynse-desktop web 部署到 www.lynse.ai + MCP 授权管理集成

## Context

把 lynse-desktop 的 `apps/web`（Next.js 16）部署到 www.lynse.ai 同源子路径 `/app/`，并在其中集成 MCP 授权管理（列表/撤销/删除授权记录）。已确认三个决策：Next 服务容器形态、lynse-mcp 桥接新增 `token-login` 动作实现免二次登录、独立页 + 设置入口。web 端裁剪桌面专属功能：本地 STT、知识库、灵感不上 web。

关键事实（已探明）：桥接 API 无 CORS、靠 nginx 同源代理；`web_origin` 校验只比 scheme+host，`/app/` 页面同源调用 `/platform/oauth/api/*` 零后端改动（cookie path=/platform/oauth 按请求路径携带）；webapp 账号 token（gapp.lynse.cn 登录）与桥接 `current_user` 验证同一后端，token-login 等同现有短信登录的信任级别。

## Part 1: lynse-mcp — token-login 桥接动作

- `lynse_remote.py`：`LOGIN_ACTIONS` 增加 `"token-login"`；`_login_action` 新分支——校验 `body["token"]`（非空字符串、≤512）→ `backend.current_user(token)` 验证 → `_save_session`（lifetime=86400，同现有钳制）→ `_refresh_grant_credentials` → 返回 `{"user": {"name": ...}}`（nickname 取自 current_user 返回）。Origin/CSRF/限流全部由 `_account` 现有包装自动继承，无新增安全面。
- `tests/test_account_oauth.py` 新增：token-login 成功建会话并可列出授权；无效 token 返回中文 AccountError；错 Origin/伪造 CSRF 403；token 不以明文落库（沿用加密存储断言风格）。
- `scripts/deploy_ai_test.sh`（新增）：把本次会话已验证的「打包 → 33,000 对齐分块 TAT 上传 → sha256 校验 → 远程 install 脚本」固化成可重复脚本，替代临时文件。
- 文档：`docs/authorization-records-api.md` 补 token-login 契约；`docs/ACCOUNT_OAUTH.md` 授权与运行边界补一句。按惯例 feature 用 feat commit，版本/CHANGELOG 留到下次 release。
- 用既有 TAT 流程重新部署测试环境（token-login 必须先于 webapp 上线）。

## Part 2: lynse-desktop — 授权管理视图 + web 裁剪

遵循 AGENTS.md：视图进 `packages/views`，shadcn tokens，导航走 `useNavigation`，注释英文，i18n 五语言键齐平（CI 强制）。

- **新共享视图** `packages/views/mcp-authorizations/`：
  - `authorizations-view.tsx`：用原生 `fetch`（`credentials: "same-origin"`，不走 ApiClient——目标不是账号后端）调用桥接；引导流程 `GET /platform/oauth/api/authorizations` → 未登录则 `POST token-login`（token 取自 `api.getToken()`，经 useAuthStore 已登录态保障；携带首 GET 下发的 `X-MCP-CSRF`）→ 重取列表。TanStack Query 管理状态，StrictMode 去重参照 McpOAuthPage 的 dedupe Map。
  - UI：Card + 列表行（client_name/client_id、scopes、状态徽章 valid/expired/revoked、授权/到期时间），撤销需 Dialog 确认，删除仅限已吊销/已过期，sonner toast 透传桥接中文错误；`isWebPlatform()` 守卫（桌面隐藏）。
  - `i18n`：`mcpAuthorizations.*` 键加进 `packages/views/locales/{zh,zh-Hant,en,ja,ko}.ts`。
- **路由** `apps/web/app/(dashboard)/authorizations/page.tsx` 薄壳（仿 settings/page.tsx）。
- **设置入口**：`settings-dialog.tsx` sections 加 `mcp_authorizations`（Shield 图标，放 `McpConfigCard` 邻位），内容为说明 + 「管理授权」按钮 → `navigation.push("/authorizations")`。
- **web 裁剪**：`lynse-sidebar.tsx` 用 `isWebPlatform()` 隐藏知识库与灵感入口（灵感已按桌面 API 探测隐藏，补知识库同款门控）；删除 `apps/web/app/(dashboard)/knowledge/` 路由；本地 STT 相关设置确认已由 `getDesktopLocalTranscriptionApi()` 空值自隐藏（web 返回 null，预计零改动，验证即可）。
- **next.config.ts**：`basePath: process.env.NEXT_PUBLIC_BASE_PATH || undefined`（不设则开发/桌面行为不变）+ `output: "standalone"`。

## Part 3: 部署（www.lynse.ai）

顺序：先 MCP 测试环境（token-login），再 webapp。

1. **MCP 测试环境**：`scripts/deploy_ai_test.sh` 一键重部署（现有管线），公网复验桥接健康。
2. **webapp 构建**：`NEXT_PUBLIC_BASE_PATH=/app NEXT_PUBLIC_API_URL=https://gapp.lynse.cn NEXT_PUBLIC_BUILD_CHANNEL=official pnpm build`（turbo → next standalone）。
3. **VPS 交付**（走 lynse-desktop 既有 ssh 模式，BFF README 的 `ssh tencent` = `ubuntu@43.130.12.243`，密钥 `~/.ssh/lynseWbEd25519.pem`）：rsync standalone 到 `~/apps/lynse-web/`；compose 服务 `node:22-alpine` 跑 `server.js`，`127.0.0.1:3102` 映射，`mem_limit 768m`，健康检查 `curl /app/`。
4. **nginx**：`lynse-desktop/deploy/www-lynse-ai/` 新增 `app-nginx.conf` 片段（`location /app/ → 127.0.0.1:3102`，含 `/app → /app/` 301、`X-Frame-Options SAMEORIGIN`）与幂等注入脚本（仿 lynse-mcp `deploy/activate-lynse-ai-test.py`：备份 → 正则定位 www.lynse.ai 443 server 块插入 include → `nginx -t` → reload，失败回滚）。执行前先验证 `ssh tencent` 连通。

## Part 4: 验证

- 本地：两仓库全量测试（lynse-mcp 72+新增；lynse-desktop `pnpm test`/`tsc`/`i18n:check`）；`pnpm build` 通过。
- 部署后机器可验：`https://www.lynse.ai/app/` 200；未登录跳登录页；桥接 smoke（无 CSRF 的 token-login 403）。
- 人工走查（需测试账号短信）：登录 webapp → `/app/authorizations` 静默建会话显示列表 → 用 test UI 授权一个 MCP 客户端 → 记录出现 → 撤销 → 客户端 token 立即失效 → 删除记录。
- 限制说明：chat/记忆/实时功能取决于 gapp 网关与 CloudBase 服务实际可用性，按可用项验收；生产 www.lynse.cn 与桌面端不动。

## 提交

- lynse-mcp：`feat: add token-login bridge action for console session bootstrap` + `docs:` 补契约 + `chore:` 部署脚本；随后 TAT 重部署测试环境。
- lynse-desktop：`feat: add MCP authorizations console view`（视图+路由+设置入口+i18n+裁剪）+ `chore: deploy web app to www.lynse.ai`（next.config/deploy 文件）。
- 不碰生产 www.lynse.cn，不动 DNS/证书。