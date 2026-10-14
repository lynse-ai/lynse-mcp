# www.lynse.cn 国内 MCP 部署记录

2026-10-10 通过已配置的阿里云 CLI OAuth profile 和 ECS 云助手完成部署。没有在聊天、脚本参数或上传包中传递阿里云密钥、业务 Token 或 MCP 加密密钥。

## 地址与实例

| 项目 | 当前值 |
| --- | --- |
| MCP 正式入口 | `https://www.lynse.cn/mcp` |
| OAuth issuer | `https://www.lynse.cn/` |
| 正式账号接口 | `https://api.lynse.cn` |
| 官网授权页 | `https://www.lynse.cn/platform/oauth`，从客户端进入 |
| ECS 公网 IP | `47.101.165.66` |
| ECS 实例 | `lynse-web` / `i-uf6j5zu4kakq7pwsnxpt` |
| 地域 | `cn-shanghai` |
| 系统 | Alibaba Cloud Linux 3，x86_64，2 CPU / 2 GB |
| Compose 项目 | `lynse-mcp-cn` |
| 容器 | `lynse-mcp-cn-mcp-1` |
| 本机监听 | `127.0.0.1:18001` → 容器 `8000` |
| 当前镜像 | `lynse-mcp-cn:20261010-072029` |

生产和 `www.lynse.ai` 测试环境分别保存授权、数据库和加密密钥。正式账号必须重新授权，测试账号授权不会迁移到正式环境。微信登录和可选网页测试客户端未启用。

## 服务器文件

| 用途 | 路径 |
| --- | --- |
| 当前 release | `/opt/lynse-mcp-cn/releases/20261010-072029` |
| 当前指针 | `/opt/lynse-mcp-cn/current` |
| 授权页与资源 | `/opt/lynse-mcp-cn/current/web` |
| Compose | `/opt/lynse-mcp-cn/current/deploy/compose-lynse-cn.yml` |
| 环境与一次生成的加密密钥 | `/etc/lynse-mcp-cn/environment`，root `0600`；父目录 `0700` |
| 加密 SQLite 数据卷 | `lynse-mcp-cn_oauth-data`，容器 `/data/oauth.sqlite3` |
| 原官网目录 | `/data/RevelSpark/dist` |
| Nginx 站点 | `/etc/nginx/conf.d/gw.conf` |
| 激活前备份 | `/opt/lynse-mcp-cn/backups/20261010-072530-gw.conf` |
| 部署记录 | `/opt/lynse-mcp-cn/last-deployment.json`，无凭据 |

当前 HTTPS block 原本使用 `server_name api.lynse.cn`，实际作为默认站点服务 `www.lynse.cn`。激活脚本按 `listen 443` 和现有网站 `root /data/RevelSpark/dist` 唯一识别它，只插入 MCP include；没有更改已有域名、TLS、首页和业务路由。

```nginx
include /opt/lynse-mcp-cn/current/deploy/nginx-lynse-cn.conf;
```

授权页独立于原官网目录，后续官网静态文件更新不会覆盖它。MCP/OAuth 路由关闭访问日志和代理缓存；授权 HTML 使用 `no-store`、`no-referrer`、CSP 和 frame 限制。

## 构建与交付

ECS 访问 Docker Hub、PyPI 官方站和 GitHub较慢。已有 Docker 镜像源能够拉取官方 `python:3.12-slim`；本次使用的基础镜像 digest 为 `sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1`。

`deploy/Dockerfile-cn` 使用阿里云 PyPI 镜像和本地准备的 `vendor/lynse_cli-1.7.0-py3-none-any.whl`。wheel 从本机已有的官方 `v1.7.0` tag 构建，提交 `78bdb37620b953a73e94fbe956845b109f5101ab`，wheel SHA256 为 `5d0fa8a3f386d5b03ee2f42627b52e4fcb7b0c1d97fd84ddd31f46b24d4d6549`。该 wheel 是服务器构建输入，不加入 npm 或 WorkBuddy 包。

运行镜像仅含五个 MCP Python 模块和必要依赖，不包含 Git、网页测试客户端、测试文件、官网源代码或 Node。容器使用普通用户、只读根目录、独立数据卷，去除 capabilities，并限制为 512 MiB / 1 CPU。

本次白名单部署归档为 153,647 字节，SHA256：

```text
bb33827c50da31367109c45881cf1d088aa53d716dfad21ed745efa9faf5430f
```

通过 ECS `SendFile` 分七片上传，使用 `DescribeSendFileResults` 验证每片，再在 ECS 合并、校验归档及逐文件 manifest。构建、26 项容器内测试和本机健康检查均通过后，激活脚本备份 Nginx、切换 release 指针并执行 `nginx -t` 和 reload。失败时恢复 Nginx 与之前的指针；安装脚本恢复之前的服务镜像，首次部署失败则停止新服务。保留数据库与密钥。

CLI 同时需要全局 `--region cn-shanghai` 和 API `--RegionId cn-shanghai`。仓库 helper 已封装这两个参数以及命令/文件的不同结果查询接口：

```sh
python3 scripts/aliyun_cli.py --instance i-uf6j5zu4kakq7pwsnxpt run /tmp/check-mcp.sh --timeout 120
python3 scripts/aliyun_cli.py --instance i-uf6j5zu4kakq7pwsnxpt poll <InvokeId>
python3 scripts/aliyun_cli.py --instance i-uf6j5zu4kakq7pwsnxpt send /tmp/chunk-000 --directory /opt/lynse-mcp-cn/uploads/<release>
```

`run` 接受脚本文件，`send` 接受单个分片；超出云助手编码大小限制时拒绝。不用通过公网开放 SSH、MCP 容器端口或额外管理页面。

参考：[阿里云 RunCommand](https://www.alibabacloud.com/help/en/ecs/developer-reference/api-ecs-2014-05-26-runcommand)、[文件传输结果查询](https://www.alibabacloud.com/help/en/ecs/developer-reference/api-ecs-2014-05-26-describesendfileresults)。

## 验证

- 本地 32 项测试、服务器 Python 3.12 生产镜像内 26 项协议测试通过。
- 容器健康为 `healthy`，Nginx 为 `active`，`nginx -t` 通过。
- 公网两个 OAuth 元数据端点返回 200，issuer/resource 均指向国内域名。
- 未登录和无效 Token 访问 MCP 均返回 401，认证挑战指向正确元数据。
- 动态注册、S256 PKCE、只读 context、Cookie/CSRF 和拒绝回调通过；拒绝回调带原始 state 和国内 issuer。
- 授权 HTML、JS、CSS、图标和官网字体加载正常；生产 `/platform/oauth/test` 返回 404。
- 既有首页仍为 200；首页文件 SHA256 仍是 `0ac00cd2bbae9248bdf5aa18afd863a2571b08f2f118f90f0a72e88c6ed9c13b`。
- 新设计的授权确认状态已部署到国内官网。

用户确认国内 MCP 使用正式账号源 `api.lynse.cn`，Codex 继续连接已经验证通过的 `www.lynse.ai/mcp`。本次额外添加的 `lynse_cn` 登录等待回调超时后已移除，保留原来已授权的 `lynse_test`。国内正式账号完整 OAuth/真实查询、WorkBuddy 安装和 Muse 真实客户端仍待各自联调。

## 运维和回退

以下在 ECS 通过云助手执行。不要输出 `environment` 的内容或运行会展开密钥的 `docker compose config`；检查结构时使用 `config --quiet`。

```sh
export LYNSE_MCP_IMAGE=lynse-mcp-cn:20261010-072029
docker compose -p lynse-mcp-cn -f /opt/lynse-mcp-cn/current/deploy/compose-lynse-cn.yml ps
curl --fail http://127.0.0.1:18001/health
nginx -t
```

要撤掉本次入口，先删除 `gw.conf` 中上述单独的 MCP include，执行 `nginx -t` 后 reload，再停止 MCP 容器。只删除此 include 可以保留后续其他网站配置变化。如果确认期间没有其他 Nginx 变更，也可以恢复本次备份：

```sh
cp /opt/lynse-mcp-cn/backups/20261010-072530-gw.conf /etc/nginx/conf.d/gw.conf
nginx -t && systemctl reload nginx
export LYNSE_MCP_IMAGE=lynse-mcp-cn:20261010-072029
docker compose -p lynse-mcp-cn -f /opt/lynse-mcp-cn/current/deploy/compose-lynse-cn.yml stop
```

不要删除 `oauth-data` 卷、不要使用 `down -v`，也不要重新生成加密密钥。后续升级使用新 release/image 标签并保留旧 release；回退镜像时同步恢复对应静态文件指针。

## WorkBuddy 国内包

```sh
python3 scripts/package_workbuddy.py --url https://www.lynse.cn/mcp --output dist/lynse-workbuddy-cn.zip
```

当前 ZIP 为 **59,447 字节**，解压 **62,130 字节**，仅有 `connector-meta.json`、`mcp.json`、`icon.png`，两者均小于 20 MB。原测试包保持连接 `www.lynse.ai`。正式账号在客户端运行时授权，包内不包含 Token。
