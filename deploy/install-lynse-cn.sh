#!/usr/bin/env bash
set -euo pipefail
release="$(cd "$(dirname "$0")/.." && pwd)"
previous_release="$(readlink -f /opt/lynse-mcp-cn/current || true)"
# A server-only update must retain the deployed website authorization bundle.
if [ ! -s "$release/web/mcp-auth.html" ]; then
    if [ ! -s "$previous_release/web/mcp-auth.html" ]; then
        printf '%s\n' 'No existing authorization page to preserve; prepare the website bundle first.' >&2
        exit 1
    fi
    mkdir -p "$release/web"
    cp -a -n "$previous_release/web/." "$release/web/"
fi
export LYNSE_MCP_IMAGE="lynse-mcp-cn:$(basename "$release")"
compose=(docker compose --project-name lynse-mcp-cn -f "$release/deploy/compose-lynse-cn.yml")
install -d -m 700 /etc/lynse-mcp-cn
# Create the key on ECS once; preserve it and the database on subsequent deploys.
python3 - <<'PY'
import base64
import os
from pathlib import Path
p = Path('/etc/lynse-mcp-cn/environment')
expected = {'LYNSE_MCP_PUBLIC_URL': 'https://www.lynse.cn/mcp',
            'LYNSE_WEB_ORIGIN': 'https://www.lynse.cn',
            'LYNSE_ACCOUNT_API_ORIGIN': 'https://api.lynse.cn',
            'LYNSE_MCP_DATABASE': '/data/oauth.sqlite3', 'LYNSE_MCP_TEST_UI': '0'}
if p.exists():
    values = dict(line.split('=', 1) for line in p.read_text().splitlines()
                  if line and not line.startswith('#') and '=' in line)
    if any(values.get(k) != v for k, v in expected.items()) or not values.get('LYNSE_MCP_ENCRYPTION_KEY'):
        raise SystemExit('Existing environment differs; preserve it and review before deployment')
else:
    values = dict(expected)
    values['LYNSE_MCP_ENCRYPTION_KEY'] = base64.urlsafe_b64encode(os.urandom(32)).decode()
    values['LYNSE_MCP_REDIRECT_SCHEMES'] = 'workbuddy,cursor,vscode'
    with p.open('x') as f:
        os.chmod(str(p), 0o600)
        f.write(''.join(k + '=' + v + '\n' for k, v in values.items()))
os.chmod(str(p), 0o600)
PY
"${compose[@]}" build --pull=false
docker run --rm --read-only --tmpfs /tmp:rw,size=32m --cap-drop ALL \
    --security-opt no-new-privileges --memory 512m \
    -v "$release:/workspace:ro" -w /workspace \
    --entrypoint /opt/venv/bin/python "$LYNSE_MCP_IMAGE" \
    -m unittest discover -s tests -q
rollback_service() {
    status=$?
    trap - ERR
    set +e
    if [ -f "$previous_release/deploy/compose-lynse-cn.yml" ]; then
        export LYNSE_MCP_IMAGE="lynse-mcp-cn:$(basename "$previous_release")"
        docker compose --project-name lynse-mcp-cn -f "$previous_release/deploy/compose-lynse-cn.yml" up -d --no-build
    else
        "${compose[@]}" stop
    fi
    exit "$status"
}
trap rollback_service ERR
"${compose[@]}" up -d --no-build
healthy=0
for attempt in $(seq 1 30); do
    if curl -fsS --max-time 3 http://127.0.0.1:18001/health >/dev/null; then
        healthy=1
        break
    fi
    sleep 2
done
if [ "$healthy" != 1 ]; then
    printf '%s\n' 'MCP health check failed; website routing was not changed.' >&2
    false
fi
python3 "$release/deploy/activate-lynse-cn.py"
trap - ERR
"${compose[@]}" ps
