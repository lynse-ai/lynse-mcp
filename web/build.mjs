import { build } from 'esbuild';
import { execFileSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const out = resolve(root, 'web/dist');
await mkdir(out, { recursive: true });
execFileSync(resolve(root, 'node_modules/.bin/tailwindcss'), [
  '-i', 'web/src/style.css', '-o', 'web/dist/style.css', '--minify',
], { cwd: root, stdio: 'inherit' });
const result = await build({
  entryPoints: [resolve(root, 'web/src/app.ts')], bundle: true, minify: true,
  format: 'iife', target: 'es2022', write: false, legalComments: 'inline',
});
const css = await readFile(resolve(out, 'style.css'), 'utf8');
const js = result.outputFiles[0].text.replaceAll('</script', '<\\/script');
await writeFile(resolve(out, 'lynse-app.html'), `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>灵光记 · Lynse</title><style>${css}</style></head>
<body><main id="app" aria-label="灵光记"><div class="boot" role="status">正在连接灵光记…</div></main><script>${js}</script></body></html>\n`);
console.log('Built web/dist/lynse-app.html (self-contained MCP Apps resource)');
