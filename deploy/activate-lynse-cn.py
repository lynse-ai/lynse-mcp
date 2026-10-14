"""Activate one ECS release while preserving the existing website and Nginx."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import time


def server_blocks(text):
    # Ignore full-line comments in this server's known configuration.
    stripped = re.sub(r'(?m)^\s*#[^\n]*', lambda m: ' ' * len(m.group()), text)
    for match in re.finditer(r'\bserver\s*\{', stripped):
        depth = 1
        for index in range(match.end(), len(stripped)):
            if stripped[index] == '{':
                depth += 1
            elif stripped[index] == '}':
                depth -= 1
                if depth == 0:
                    yield match, stripped[match.start():index + 1]
                    break


def activate():
    release = Path(__file__).resolve().parent.parent
    base = Path('/opt/lynse-mcp-cn')
    pointer = base / 'current'
    previous = os.readlink(str(pointer)) if pointer.is_symlink() else None
    if pointer.exists() and not pointer.is_symlink():
        raise SystemExit('Current release path must be a symlink')
    site = Path('/etc/nginx/conf.d/gw.conf')
    original = site.read_text()
    marker = 'include /opt/lynse-mcp-cn/current/deploy/nginx-lynse-cn.conf;'
    candidates = [(m, block) for m, block in server_blocks(original)
                  if re.search(r'listen\s+443\b', block)
                  and re.search(r'root\s+/data/RevelSpark/dist\s*;', block)]
    if len(candidates) != 1:
        raise SystemExit('Expected exactly one existing website HTTPS server; no config changed')
    match, block = candidates[0]
    if marker in original and marker not in block:
        raise SystemExit('MCP include exists in an unexpected server; no config changed')
    homepage = Path('/data/RevelSpark/dist/index.html')
    digest = hashlib.sha256(homepage.read_bytes()).hexdigest()
    backups = base / 'backups'
    backups.mkdir(exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S', time.gmtime())
    backup = backups / (stamp + '-gw.conf')
    shutil.copy2(str(site), str(backup))
    updated = original if marker in block else original[:match.end()] + '\n    ' + marker + original[match.end():]
    if site.read_text() != original:
        raise SystemExit('Nginx configuration changed concurrently; activation stopped')
    pending = base / '.current-next'
    pending.symlink_to(release)
    os.replace(str(pending), str(pointer))
    try:
        site.write_text(updated)
        subprocess.run(['nginx', '-t'], check=True)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
    except BaseException:
        shutil.copy2(str(backup), str(site))
        pointer.unlink()
        if previous:
            pointer.symlink_to(previous)
        subprocess.run(['nginx', '-t'], check=False)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=False)
        raise
    if hashlib.sha256(homepage.read_bytes()).hexdigest() != digest:
        raise SystemExit('Homepage changed concurrently; inspect the website deployment')
    record = {'release': str(release), 'image': os.environ['LYNSE_MCP_IMAGE'],
              'previous_release': previous, 'nginx_backup': str(backup),
              'homepage_sha256': digest, 'resource': 'https://www.lynse.cn/mcp',
              'account_backend': 'https://api.lynse.cn'}
    (base / 'last-deployment.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record))


if __name__ == '__main__':
    activate()
