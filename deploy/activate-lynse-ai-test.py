"""Install a narrow HTTPS server include; restore the current config on failure."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
import time

source = Path(__file__).resolve().parent
site = Path('/etc/nginx/sites-enabled/revelspark').resolve()
current = site.read_text()
stamp = time.strftime('%Y%m%d-%H%M%S', time.gmtime())
backup = Path('/opt/lynse-mcp-ai-test/backups') / (stamp + '-revelspark.conf')
shutil.copy2(site, backup)
homepage = Path('/var/www/revelspark/index.html')
digest = hashlib.sha256(homepage.read_bytes()).hexdigest()
snippets = Path('/etc/nginx/snippets')
snippets.mkdir(exist_ok=True)
for name in ('nginx-lynse-ai-test.conf', 'nginx-lynse-ai-test-proxy.conf'):
    target = snippets / name.replace('nginx-', 'lynse-mcp-')
    # Keep the include names stable and aligned with the snippet contents.
    target = snippets / ('lynse-mcp-ai-test-proxy.conf' if 'proxy' in name else 'lynse-mcp-ai-test.conf')
    if target.exists():
        shutil.copy2(target, backup.with_name(stamp + '-' + target.name))
    shutil.copy2(source / name, target)
marker = 'include /etc/nginx/snippets/lynse-mcp-ai-test.conf;'
if marker not in current:
    match = next(re.finditer(r'^\s*server_name\s+www\.lynse\.ai\s+lynse\.ai\s*;[^\n]*\n', current, re.M), None)
    if match is None:
        raise SystemExit('Website HTTPS server was not found; no site config changed')
    begin = current.rfind('server {', 0, match.start())
    depth, end = 0, None
    for index in range(current.index('{', begin), len(current)):
        if current[index] == '{': depth += 1
        elif current[index] == '}':
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    block = current[begin:end]
    if not re.search(r'listen\s+(?:\[::\]:)?443\b', block):
        raise SystemExit('Refusing to modify a non-HTTPS server block')
    updated = current[:match.end()] + '    ' + marker + '\n' + current[match.end():]
    if site.read_text() != current:
        raise SystemExit('Nginx config changed concurrently; deployment stopped')
    site.write_text(updated)
try:
    subprocess.run(['nginx', '-t'], check=True)
    subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
except BaseException:
    shutil.copy2(backup, site)
    subprocess.run(['nginx', '-t'], check=False)
    raise
if hashlib.sha256(homepage.read_bytes()).hexdigest() != digest:
    raise SystemExit('Homepage changed concurrently; verify the other deployment')
record = {'homepage_sha256': digest, 'nginx_backup': str(backup), 'resource': 'https://www.lynse.ai/mcp',
          'account_backend': 'https://gapp.lynse.cn'}
Path('/opt/lynse-mcp-ai-test/last-deployment.json').write_text(json.dumps(record, indent=2))
print(json.dumps(record))
