"""Build the standalone deployment package for the online long-term
authorization service (OAuth grants + records console + remote MCP resource).

The package mirrors the running production image (root Dockerfile runtime
stage): explicit member list only, so caches, local data and secrets can never
enter the archive. README and .env.example are generated here to stay in sync
with `lynse_remote.Settings.from_env`."""
import argparse
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
# Closed import set verified against lynse_remote.create_app -> lynse_mcp ->
# lynse_auth/lynse_account/lynse_oauth_store (+ standalone lynse_ui asset host).
CODE_FILES = (
    "lynse_remote.py",
    "lynse_mcp.py",
    "lynse_account.py",
    "lynse_oauth_store.py",
    "lynse_auth.py",
    "lynse_ui.py",
)
ASSET_FILES = ("web/dist/lynse-app.html",)
DEPLOY_FILES = ("Dockerfile", "compose.yml",
                "requirements.txt", "requirements-mcp.txt", "requirements-remote.txt")
DOC_FILES = ("docs/BACKEND_MCP_REFRESH_PLAN.md", "docs/authorization-records-api.md",
             "docs/ACCOUNT_OAUTH.md")

README_TEMPLATE = """# Lynse MCP 长期账号授权服务（独立部署包）

灵光记线上 MCP 长期账号授权服务的独立打包，版本 {version}。与线上镜像（www.lynse.ai / www.lynse.cn）同一构建：官网 OAuth 授权页（DCR + PKCE + 同意页）、90 天绝对期 + 30 天空闲滑动的 grant、MCP Token 轮换与重放撤销、「我的授权」控制台、远程 MCP 资源服务（Streamable HTTP，23 个只读工具与 UI 资源）。

## 包内容

```text
lynse_remote.py        # HTTP 服务：OAuth 提供方、官网登录桥接、授权控制台、MCP 挂载
lynse_mcp.py           # MCP 工具与资源服务（被远程应用挂载）
lynse_account.py       # Lynse 账号后端适配（登录、身份、业务调用）
lynse_oauth_store.py   # 加密 SQLite 存储（grant/token/授权记录）
lynse_auth.py          # 本地 Key 认证流程（本地 stdio 模式复用，远程包内保留）
lynse_ui.py            # MCP Apps UI 资源宿主
web/dist/lynse-app.html
Dockerfile / compose.yml / requirements*.txt
.env.example           # 环境变量模板（复制为 .env 后填写）
docs/                  # 后端统一计划 v2.0、授权记录 API 契约、账号 OAuth 说明
```

## 运行

```bash
cp .env.example .env   # 必填 LYNSE_MCP_ENCRYPTION_KEY，按环境改三个 origin
docker compose up -d --build
```

服务监听 127.0.0.1:8000/health，由前置 Nginx 反代 `/mcp` 与 `/platform/oauth`；数据卷 `oauth-data` 挂载 `/data`，`LYNSE_MCP_DATABASE` 指向 `/data/oauth.sqlite3`。镜像以非 root、只读根文件系统运行。手动运行（需 Python 3.12，安装 requirements-remote.txt）：`python lynse_remote.py --host 0.0.0.0 --port 8000`。

## 环境变量

| 变量 | 说明 |
| --- | --- |
| `LYNSE_MCP_PUBLIC_URL` | MCP 公网地址，必须以 `/mcp` 结尾（如 `https://www.lynse.cn/mcp`） |
| `LYNSE_WEB_ORIGIN` | 官网 origin，无路径无尾斜杠 |
| `LYNSE_ACCOUNT_API_ORIGIN` | 账号后端 origin（正式 `https://api.lynse.cn`，测试 `https://gapp.lynse.cn`） |
| `LYNSE_MCP_ENCRYPTION_KEY` | store 加密密钥（必填，base64，泄露即等同全部授权泄露） |
| `LYNSE_MCP_DATABASE` | SQLite 路径，容器内固定 `/data/oauth.sqlite3` |
| `LYNSE_MCP_GRANT_DAYS` / `LYNSE_MCP_REFRESH_IDLE_DAYS` | 授权绝对期（默认 90）/ 空闲滑动期（默认 30），空闲不得超过绝对期 |
| `LYNSE_MCP_REDIRECT_SCHEMES` | 允许的自定义回调 scheme，默认 `workbuddy,cursor,vscode` |
| `LYNSE_WECHAT_CALLBACK_URL` / `LYNSE_WECHAT_AUTH_TYPE` | 微信扫码回调与类型，未配置则官网不展示微信入口 |
| `LYNSE_MCP_TEST_UI` | `1` 时开放 `/platform/oauth/test/authorizations` 自测管理页，生产必须 `0` |

## 与后端统一的关系

本包是当前在线运行的事实标准（自建授权体系）。上游凭据续期、撤销传播、账号安全事件联动尚未由账号后端提供；统一方案与接口契约见 `docs/BACKEND_MCP_REFRESH_PLAN.md`（v2.0）。MCP 侧 `/revoke` 与控制台撤销本地立即生效，但已签发上游 Token 在业务 API 侧仍有效至自然过期。

安全事件（密钥泄露、需全量失效）时：更换 `LYNSE_MCP_ENCRYPTION_KEY` 并清空数据卷即等同撤销全部授权，所有客户端需重新官网授权。
"""

ENV_EXAMPLE = """# Lynse MCP authorization service configuration (copy to .env)
# All origins must be https, no path, no trailing slash.
LYNSE_MCP_PUBLIC_URL=https://www.lynse.cn/mcp
LYNSE_WEB_ORIGIN=https://www.lynse.cn
LYNSE_ACCOUNT_API_ORIGIN=https://api.lynse.cn
# Required: base64 key, e.g. python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
LYNSE_MCP_ENCRYPTION_KEY=
LYNSE_MCP_DATABASE=/data/oauth.sqlite3
LYNSE_MCP_GRANT_DAYS=90
LYNSE_MCP_REFRESH_IDLE_DAYS=30
LYNSE_MCP_REDIRECT_SCHEMES=workbuddy,cursor,vscode
LYNSE_WECHAT_CALLBACK_URL=
LYNSE_WECHAT_AUTH_TYPE=WECHAT_JUMP
LYNSE_MCP_TEST_UI=0
"""


def read_version() -> str:
    text = (ROOT / "lynse_mcp.py").read_text(encoding="utf-8")
    match = re.search(r'MCP_VERSION = "([^"]+)"', text)
    if not match:
        raise ValueError("Cannot determine service version from lynse_mcp.py")
    return match.group(1)


def build_package(output: Path, *, version: str) -> dict:
    members = {}
    for name in CODE_FILES + ASSET_FILES + DEPLOY_FILES + DOC_FILES:
        members[f"lynse-auth-service-{version}/{name}"] = (ROOT / name).read_bytes()
    members[f"lynse-auth-service-{version}/.env.example"] = ENV_EXAMPLE.encode("utf-8")
    members[f"lynse-auth-service-{version}/README.md"] = README_TEMPLATE.format(version=version).encode("utf-8")
    unpacked = sum(len(data) for data in members.values())
    output.parent.mkdir(parents=True, exist_ok=True)
    # Explicit members prevent local secrets, caches and runtimes entering the ZIP.
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in members.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    size = output.stat().st_size
    return {"path": str(output), "bytes": size, "unpacked_bytes": unpacked,
            "files": len(members), "version": version}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=None, help="Output ZIP path (default dist/lynse-auth-service-<version>.zip)")
    args = parser.parse_args()
    version = read_version()
    output = Path(args.output) if args.output else ROOT / "dist" / f"lynse-auth-service-{version}.zip"
    result = build_package(output, version=version)
    print(f"lynse-auth-service {result['version']}: {result['files']} files, "
          f"{result['bytes']:,} bytes (unpacked {result['unpacked_bytes']:,})")
    print(f"written to {result['path']}")


if __name__ == "__main__":
    main()
