#!/bin/sh
# Executed by TAT as root in an uploaded, isolated release directory.
set -eu
cd "$(dirname "$0")/.."
release=$(pwd)
mkdir -p /etc/lynse-mcp-ai-test /opt/lynse-mcp-ai-test/backups
chmod 700 /etc/lynse-mcp-ai-test
python3 - <<'PY'
import base64
import os
from pathlib import Path
p = Path('/etc/lynse-mcp-ai-test/environment')
if not p.exists():
    values = {
        'LYNSE_MCP_PUBLIC_URL': 'https://www.lynse.ai/mcp',
        'LYNSE_WEB_ORIGIN': 'https://www.lynse.ai',
        'LYNSE_ACCOUNT_API_ORIGIN': 'https://gapp.lynse.cn',
        'LYNSE_MCP_DATABASE': '/data/oauth.sqlite3',
        'LYNSE_MCP_ENCRYPTION_KEY': base64.urlsafe_b64encode(os.urandom(32)).decode(),
        'LYNSE_WECHAT_CALLBACK_URL': '',
        'LYNSE_MCP_TEST_UI': '1',
    }
    fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as out:
        out.write(''.join(k+'='+v+'\n' for k,v in values.items()))
else:
    values = dict(line.split('=',1) for line in p.read_text().splitlines() if '=' in line)
    assert values.get('LYNSE_MCP_PUBLIC_URL') == 'https://www.lynse.ai/mcp'
    assert values.get('LYNSE_ACCOUNT_API_ORIGIN') == 'https://gapp.lynse.cn'
    assert values.get('LYNSE_MCP_TEST_UI') == '1'
print('Test environment prepared; encryption key retained privately')
PY
docker compose -p lynse-mcp-ai-test -f deploy/compose-lynse-ai-test.yml config --quiet
if ! docker compose -p lynse-mcp-ai-test -f deploy/compose-lynse-ai-test.yml build > build.log 2>&1; then
    tail -50 build.log
    exit 1
fi
echo 'Container image built'
if ! docker run --rm --memory 384m --user root --entrypoint /bin/sh \
    -v "$release/tests:/tests:ro" lynse-mcp-ai-test:latest \
    -c 'pip install -q "httpx>=0.28.1,<1" && python -m unittest discover -s /tests -v' > image-tests.log 2>&1; then
    tail -55 image-tests.log
    exit 1
fi
tail -4 image-tests.log
docker compose -p lynse-mcp-ai-test -f deploy/compose-lynse-ai-test.yml up -d
python3 - <<'PY'
import json
import time
import urllib.request
for attempt in range(30):
    try:
        with urllib.request.urlopen('http://127.0.0.1:18000/health', timeout=2) as r:
            assert json.load(r)['status'] == 'ok'
        print('MCP health check passed')
        break
    except Exception:
        if attempt == 29:
            raise
        time.sleep(1)
PY
mkdir -p /var/www/revelspark/platform/oauth-static
cp -a web/. /var/www/revelspark/platform/oauth-static/
chmod -R a+rX /var/www/revelspark/platform/oauth-static
python3 deploy/activate-lynse-ai-test.py
ln -sfn "$release" /opt/lynse-mcp-ai-test/current
docker compose -p lynse-mcp-ai-test -f deploy/compose-lynse-ai-test.yml ps
